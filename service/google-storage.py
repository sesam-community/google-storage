from flask import Flask, Response, request, abort
import datetime
from datetime import datetime as dt
import json
import os
import logging
import google.auth
from google.cloud import storage
from openssl_signer import OpenSSLSigner

app = Flask(__name__)
DT_PATTERN = os.environ.get('DT_PATTERN', "%Y-%m-%d %H:%M:%S.%f%z")
LIMIT = int(os.environ.get("LIMIT")) if os.environ.get("LIMIT") else None
FIELDS = "nextPageToken,items(name,generation,updated)"

if os.environ.get("PROFILE"):
    from werkzeug.middleware.profiler import ProfilerMiddleware

    app.config['PROFILE'] = True
    app.wsgi_app = ProfilerMiddleware(app.wsgi_app, restrictions=[50])

__version__ = '0.0.3'

# Get env.vars
credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")

# Set up logging
log_level = logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))
logging.basicConfig(level=log_level)  # dump log to stdout

# write out service config from env var to known file
if credentials:
    with open(credentials_path, "wb") as out_file:
        out_file.write(credentials.encode())


@app.route("/datasets/<bucket_name>/entities", methods=["GET"])
def get_entities(bucket_name):
    logging.info(f"serving request {request} for bucket {bucket_name}")
    """
    Endpoint to read entities from gcp bucket, add signed url and return
    Available query parameters (all optional)
        expire: date time in format %Y-%m-%d %H:%M:%S - overrides default expire time
        with_subfolders: False by default if assigned will include blobs from subfolders
        with_prefix: optional, to filter blobs
        since : will be compared with update field for each file and only newer files will be returned if set
        do_not_sign: will not create signed url's if set to true 
    :return:
    """

    set_expire = request.args.get('expire')
    with_subfolders = request.args.get('with_subfolders')
    with_prefix = request.args.get('with_prefix')
    since = request.args.get('since')
    do_not_sign = bool(request.args.get('do_not_sign', False))

    if since is not None:
        logging.info(f'got since: {since}')
        try:
            # dirty hack for timezone
            # by default we get datetime where timezone part contains : and python datetime doesn't support it
            # by default since contains + in timezone part i.e. +02:00
            # and + becomes space " " when it sent as HTTP query param
            if ":" == since[-3]:
                since = since[:-3] + since[-2:]
                since = since[:-6] + '+' + since[-4:]
            since = dt.strptime(since, DT_PATTERN)
        except ValueError as e:
            logging.warning(e)
            logging.warning(f"couldn't parse datetime from since: {since}")
            since = None

    def generate():
        """Lists all the blobs in the bucket."""
        count = 0

        credentials_obj, _ = google.auth.default()
        new_signer = OpenSSLSigner.from_service_account_file(credentials_path)
        credentials_obj._signer = new_signer

        if not set_expire:
            expiration = datetime.datetime(2183, 9, 8, 13, 15)
        else:
            expiration = datetime.datetime.strptime(set_expire, '%Y-%m-%d %H:%M:%S')

        iterator = storage_client.list_blobs(bucket_name, prefix=with_prefix, max_results=LIMIT, fields=FIELDS)

        first = True
        yield "["

        while True:
            for blob in iterator:
                if since is not None and blob.updated < since:
                    continue

                entity = {"_id": blob.name}
                if '/' in entity["_id"] and not with_subfolders:  # take only root folder
                    continue

                if entity["_id"].endswith("/"):  # subfolder object
                    logging.info(f'skipping folder object {blob.name}')
                    continue

                entity["file_id"] = entity["_id"]

                if not do_not_sign:
                    entity["file_url"] = blob.generate_signed_url(expiration, method="GET")

                entity["updated"] = str(blob.updated)
                entity["_updated"] = entity["updated"]
                entity["generation"] = blob.generation

                if not first:
                    yield ","
                yield json.dumps(entity)

                count += 1
                first = False
            next_page_token = iterator.next_page_token

            logging.info(f'batch of {iterator.num_results} items successfully processed')
            if next_page_token is None or iterator.num_results < LIMIT:
                break

            iterator = storage_client.list_blobs(bucket_name, prefix=with_prefix, page_token=next_page_token,
                                                 max_results=LIMIT, fields=FIELDS)
        yield "]"
        logging.info(f"{count} elements processed")

    try:
        storage_client = storage.Client()
        response = Response(generate(), mimetype="application/json")
        return response
    except Exception as e:
        logging.error(f'error occurred while listing blobs due to: {str(e)}')
        abort(e.code, e.message)


@app.route("/download/<bucket>/<path:filename>", methods=['GET'])
def download(bucket, filename):
    """
    Downloads file with given name from given bucket
    :param bucket: Google cloud storage bucket name
    :param filename: path to file (simply file name for files in root folder)
    :return: file
    """
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket)
    try:
        logging.debug(f'trying to download file {filename} from {bucket}')
        chunk_size = 262144 * 4 * 10
        blob = bucket.blob(filename, chunk_size=chunk_size)

        def generate():
            file_data = blob.download_as_string(start=0, end=chunk_size)
            yield file_data
            counter = chunk_size + 1  # both start and end are inclusive
            while len(file_data) >= chunk_size:
                file_data = blob.download_as_string(start=counter, end=counter + chunk_size - 1)
                yield file_data
                counter += chunk_size

        return Response(generate(), headers={'Content-Type': blob.content_type})
    except Exception as e:
        logging.error(f'error occurred while downloading blob due to: {str(e)}')
        abort(e.code, e.message)


@app.route("/upload/<bucket_name>", methods=["POST"])
def upload(bucket_name):
    """
    Upload file to given bucket
    :param bucket_name:
    :return: 200 code if everything OK
    """
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    files = request.files

    local_path = request.headers.get('local_path')

    for file in files:
        if files[file].filename == '':
            continue
        filename = files[file].filename

        content_type = files[file].content_type
        logging.info(f"uploading {filename} to {local_path}")
        if local_path:
            filename = f"{local_path}/{filename}"
        blob = bucket.blob(filename)
        blob.content_type = content_type
        blob.upload_from_file(files[file])
    return Response()


@app.route("/sink/<bucket_name>", methods=["POST"])
def sink(bucket_name):
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)
    entities = request.get_json()

    try:
        for entity in entities:
            filename = entity['filename']
            data = entity['data']

            blob = bucket.blob(filename)

            if entity['_deleted']:
                try:
                    blob.delete()
                    logger.info('File {} deleted from bucket.'.format(filename))
                except google.api_core.exceptions.NotFound:
                    logger.info('File {} does not exist in bucket.'.format(filename))
            else:
                blob.upload_from_string(json.dumps(data).encode("utf-8"), content_type="application/json")
                logger.info('File uploaded to {}.'.format(filename))
    except Exception as e:
        logger.error(str(e))
        abort(type(e).__name__, str(e))

    return Response()


if __name__ == "__main__":
    # Set up logging
    format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logger = logging.getLogger("google-storage-microservice")

    # Log to stdout
    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(logging.Formatter(format_string))
    logger.addHandler(stdout_handler)
    debug = True if os.environ.get("PROFILE") or os.environ.get('DEBUG') else False
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logging.info(f"starting service v.{__version__}")
    port = os.environ.get('PORT', 5000)
    if debug:
        app.run(threaded=True, debug=debug, host='0.0.0.0', port=port)
    else:
        import cherrypy

        cherrypy.tree.graft(app, '/')

        # Set the configuration of the web server
        cherrypy.config.update({
            'environment': 'production',
            'engine.autoreload_on': False,
            'log.screen': False,
            'server.socket_port': int(port),
            'server.socket_host': '0.0.0.0',
            'server.thread_pool': 10
        })

        # Start the CherryPy WSGI web server
        cherrypy.engine.start()
        cherrypy.engine.block()
