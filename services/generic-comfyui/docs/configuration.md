# Configuration

This document outlines the environment variables available for configuring the `worker-comfyui`.

## General Configuration

| Environment Variable | Description                                                                                                                                                                                                                  | Default |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| `REFRESH_WORKER`     | When `true`, the worker pod will stop after each completed job to ensure a clean state for the next job. See the [RunPod documentation](https://docs.runpod.io/docs/handler-additional-controls#refresh-worker) for details. | `false` |
| `SERVE_API_LOCALLY`  | When `true`, enables a local HTTP server simulating the RunPod environment for development and testing. See the [Development Guide](development.md#local-api) for more details.                                              | `false` |
| `COMFY_ORG_API_KEY`  | Comfy.org API key to enable ComfyUI API Nodes. If set, it is sent with each workflow; clients can override per request via `input.comfy_org_api_key` or `input.api_key_comfy_org`.                                           | –       |
| `INPUT_DOWNLOAD_TIMEOUT_S` | Seconds to wait for the next bytes while downloading input media from HTTP(S) URLs such as presigned S3 links. Connecting has its own limit of 10 seconds. | `300` |
| `INPUT_DOWNLOAD_MAX_BYTES` | Largest input file fetched from a URL. The file is held in memory before it is handed to ComfyUI; a larger one fails the job. At most five redirects are followed, with their bodies left unread, and a response with a `Content-Encoding` is refused. | `268435456` (256 MiB) |
| `COMFY_DEVICE` | `cpu` starts ComfyUI with `--cpu` and skips the GPU pre-flight check. Set by the image built from `Dockerfile.cpu`; leave unset on a GPU image. | `gpu` |
| `WORKFLOW_EXECUTION_TIMEOUT_S` | Maximum time in seconds to wait for a queued ComfyUI workflow. On expiry, the worker requests `/interrupt` and returns a timeout error. Set to `0` to disable the watchdog.                                      | `1200`  |
| `COMFY_API_VIDEO_DOWNLOAD_TIMEOUT_S` | Maximum time in seconds for each ComfyUI API video download attempt.                                                                                                                                    | `600`   |
| `COMFY_API_VIDEO_DOWNLOAD_IDLE_TIMEOUT_S` | Maximum time in seconds that an API video download may make no progress before retrying the same result URL.                                                                                             | `60`    |
| `COMFY_API_VIDEO_DOWNLOAD_MAX_RETRIES` | Number of retries of the same API video result URL after the initial attempt. Retrying the download does not submit another generation.                                                                    | `2`     |

## Logging Configuration

| Environment Variable   | Description                                                                                                                                                      | Default |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| `COMFY_LOG_LEVEL`      | Controls ComfyUI's internal logging verbosity. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Use `DEBUG` for troubleshooting, `INFO` for production. | `DEBUG` |
| `NETWORK_VOLUME_DEBUG` | Enable detailed network volume diagnostics in worker logs. Useful for debugging model path issues. See [Network Volumes & Model Paths](network-volumes.md).      | `false` |
| `RUNPOD_LOG_LEVEL`     | Verbosity of the RunPod SDK's own log lines: `ERROR`, `WARN`, `INFO`, `DEBUG` or `TRACE`. The handler sets `INFO` when the variable is not set or blank. See the note below before choosing `DEBUG`. | `INFO`  |

At `DEBUG`, which is the SDK's own default, the SDK logs each job's whole output, and with S3 upload configured that output holds the presigned result links. A presigned link is a credential for as long as it is valid (a week), so set `RUNPOD_LOG_LEVEL=DEBUG` on an endpoint only for a short diagnosis and treat that endpoint's logs accordingly. The SDK reads the variable once, at start, so a change needs new workers. Its older name, `RUNPOD_DEBUG_LEVEL`, no longer changes the level on its own.

For the same reason the message of a failed node is logged, and returned in `errors` or `details`, with the query string of every URL in it cut: `https://host/path?[redacted]`. HTTP clients put the request URL into their error text, and a provider's result link is signed. The same cut is applied to the strings in `comfy_credits.details`, which are collected from the whole ComfyUI history. An unsigned URL loses its query string as well.

With `BUCKET_ENDPOINT_URL` set and one of its two keys missing, a job is refused before its workflow runs: the worker could not deliver the result, and the RunPod SDK would return a path on the worker's disk as if it were the link. A video, an audio file or a text file is stored with its own content type; an input a loader node reports back, such as the clip of `LoadVideo`, is not stored or returned. The suite's [job contract](../../../docs/handler-contract.md) describes the whole answer.

## Debugging Configuration

| Environment Variable           | Description                                                                                                            | Default |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------- | ------- |
| `WEBSOCKET_RECONNECT_ATTEMPTS` | Number of websocket reconnection attempts when connection drops during job execution.                                  | `5`     |
| `WEBSOCKET_RECONNECT_DELAY_S`  | Delay in seconds between websocket reconnection attempts.                                                              | `3`     |
| `WEBSOCKET_TRACE`              | Enable low-level websocket frame tracing for protocol debugging. Set to `true` only when diagnosing connection issues. | `false` |

## AWS S3 Upload Configuration

Configure these variables **only** if you want the worker to upload generated output media directly to an AWS S3 bucket. If these are not set, images, videos, audio, and files will be returned as base64-encoded strings in the API response.

- **Prerequisites:**
  - An AWS S3 bucket in your desired region.
  - An AWS IAM user with programmatic access (Access Key ID and Secret Access Key).
  - Permissions attached to the IAM user allowing `s3:PutObject` (and potentially `s3:PutObjectAcl` if you need specific ACLs) on the target bucket.

| Environment Variable       | Description                                                                                                                             | Example                                                    |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `BUCKET_ENDPOINT_URL`      | The full endpoint URL of your S3 bucket. **Must be set to enable S3 upload.**                                                           | `https://<your-bucket-name>.s3.<aws-region>.amazonaws.com` |
| `BUCKET_ACCESS_KEY_ID`     | Your AWS access key ID associated with the IAM user that has write permissions to the bucket. Required if `BUCKET_ENDPOINT_URL` is set. | `AKIAIOSFODNN7EXAMPLE`                                     |
| `BUCKET_SECRET_ACCESS_KEY` | Your AWS secret access key associated with the IAM user. Required if `BUCKET_ENDPOINT_URL` is set.                                      | `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`                 |

**Note:** Upload uses the `runpod` Python library helper `rp_upload.upload_image`, which handles creating a unique path within the bucket based on the `job_id`.

### Example S3 Response

If the S3 environment variables (`BUCKET_ENDPOINT_URL`, `BUCKET_ACCESS_KEY_ID`, `BUCKET_SECRET_ACCESS_KEY`) are correctly configured, a successful job response will look similar to this:

```json
{
  "id": "sync-uuid-string",
  "status": "COMPLETED",
  "output": {
    "images": [
      {
        "filename": "ComfyUI_00001_.png",
        "type": "s3_url",
        "data": "https://your-bucket-name.s3.your-region.amazonaws.com/sync-uuid-string/ComfyUI_00001_.png"
      }
      // Additional images generated by the workflow would appear here
    ]
    // The "errors" key might be present here if non-fatal issues occurred
  },
  "delayTime": 123,
  "executionTime": 4567
}
```

The `data` field contains the presigned URL to the uploaded image file in your S3 bucket. The path usually includes the job ID.
