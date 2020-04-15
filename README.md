# google-storage

[![Build Status](https://travis-ci.org/sesam-community/google-storage.svg?branch=master)](https://travis-ci.org/sesam-community/google-storage)

## Environment variables

`GOOGLE_APPLICATION_CREDENTIALS` - Path to Google Storage credential file (json format file for service account authentication). 

`GOOGLE_APPLICATION_CREDENTIALS_CONTENT` - Content of Google Storage credential file. This content is written to the file specified in `GOOGLE_APPLICATION_CREDENTIALS`. 

`DT_PATTERN` - since datetime pattern, by default "%Y-%m-%d %H:%M:%S.%f%z"  

`LIMIT` - if limit list result to LIMIT entities and use paging to retrieve multiple batches, not set by default  
## Usage

The microservice listens to the following endpoints at port 5000:

`/upload` - JSON formatted config for where it is to fetch source files for upload to google storage

`/datasets/<bucket name>/entities` - returns JSON formatted entities listing all files at the google storage from bucket with <bucket name>
Available query parameters (all optional)  
* expire: date time in format %Y-%m-%d %H:%M:%S - overrides default expire time
* with_subfolders: False by default if assigned will include blobs from subfolders
* with_prefix: to filter blobs by prefix
* since : will be compared with update field for each file and only newer files will be returned if set
* do_not_sign: will not create signed url's if set to true 
```
[
    {
        "file_id": "some-file-name",
        "content_type": your_content_type",
        "file_url": "url/to/file/" <- signed URL with expiration date
    },
    ...
]
```

`/download/<bucket name>/<file path>` - downloads file from Google Storage Bucket  
file path may include slashes to download file from bucket sub-folder

`/upload/<bucket_name>` - uploads file to bucket

`/sink/<bucket_name>` - Sesam json push sink for sending json entities to a Google Storage Bucket and save them as files.
entities must have the data and filename properties, data should contain the json to be written to the file, filename should be the name of the file.

## Example System Config
```
{
  "_id": "google-storage-microservice",
  "type": "system:microservice",
  "docker": {
    "environment": {
      "GOOGLE_APPLICATION_CREDENTIALS": "google-store-credentials.json",
      "GOOGLE_APPLICATION_CREDENTIALS_CONTENT": "$SECRET(google-storage-credential-content)"
    },
    "image": "sesamcommunity/google-storage:latest",
    "port": 5000
  }
}
```

## Example Pipe Config
```json
{
  "_id": "<pipe id>",
  "type": "pipe",
  "source": {
    "type": "conditional",
    "alternatives": {
      "prod": {
        "type": "json",
        "system": "<system id>",
        "is_since_comparable": true,
        "supports_since": true,
        "url": "/datasets/<path to GCP bucket>/entities?with_subfolders=true"
      },
      "test": {
        "type": "embedded",
        "entities": []
      }
    },
    "condition": "$ENV(current-env)"
  },
  "transform": {
    "type": "dtl",
    "rules": {
      "default": [
        ["copy", "*"]
      ]
    }
  },
  "pump": {
    "cron_expression": "0 0 * * ?"
  }
}

``` 
