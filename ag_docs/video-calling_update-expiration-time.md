---
title: Update expiration time
description: API reference for updating the expiration time of a user banning rule
sidebar_position: 3
platform: android
exported_from: https://docs.agora.io/en/video-calling/channel-management-api/endpoint/ban-user-privileges/update-expiration-time
exported_on: '2026-01-20T05:57:36.453822Z'
exported_file: update-expiration-time.md
---

[HTML Version](https://docs.agora.io/en/video-calling/channel-management-api/endpoint/ban-user-privileges/update-expiration-time)

# Update expiration time

This method updates the expiration time of a specified banning rule.

### Prototype

- Method: `PUT`
- Endpoint: `https://api.agora.io/dev/v1/kicking-rule`

### Request parameters

**Request header**

The `Content-Type` field in all HTTP request headers is `application/json`. All requests and responses are in JSON format. All request URLs and request bodies are case-sensitive.

The Agora Channel Management RESTful APIs only support HTTPS. Before sending HTTP requests, you must generate a Base64-encoded credential with the **Customer ID** and **Customer Secret** provided by Agora, and pass the credential to the `Authorization` field in the HTTP request header. See [RESTful authentication](https://docs-md.agora.io/en/video-calling/channel-management-api/restful-authentication.md) for details.

**Request body**

Pass in the following parameters in the request body:

```json
{
  "appid": "4855xxxxxxxxxxxxxxxxxxxxxxxxeae2",
  "id": 1953,
  "time": 60
}
```

| Parameter     | Type |Required/Optional  | Description                     |
| :---------------- | :-----|:-----  | :--------------------------------------- |
| `appid`           | String | Required | The App ID of the project. You can get it through one of the following methods:<ul><li>Copy from the [Agora Console](https://console.agora.io/v2).</li><li> Call the [Get all projects](https://docs-md.agora.io/en/interactive-live-streaming/reference/agora-console-rest-api.md) API, and read the value of the `vendor_key` field in the response body.</li></ul> |
| `id`              | Number | Required |The ID of the rule that you want to update.                                |
| `time`            | Number | Required | The time duration (in minutes) to ban the user. The value range is [1,1440].<br/> <ul><li>If the set value is between `0` and `1`, Agora automatically sets the value to `1`.</li><li>If the set value is greater than `1440`, Agora automatically sets the value to `1440`.</li><li>If the set value is `0`, the banning rule does not take effect. The server sets all users that conform to the rule offline, and users can log in again to rejoin the channel.</li><li>Use either `time` or `time_in_seconds`. If you set both parameters, the `time_in_seconds` parameter takes effect; if you set neither of these parameters, the Agora server automatically sets the banning time duration to 60 minutes, that is, 3600 seconds.</li></ul>|
| `time_in_seconds` | Number | Required | The time duration (in seconds) to ban the user. The value range is [10,86430].<ul><li>If the set value is between `0` and `10`, Agora automatically sets the value to `10`.</li><li>If the set value is greater than `86430`, Agora automatically sets the value to `86430`.</li><li>If the set value is `0`, the banning rule does not take effect. The server sets all users that conform to the rule offline, and users can log in again to rejoin the channel.</li><li>Use either `time` or `time_in_seconds`. If you set both parameters, the `time_in_seconds` parameter takes effect; if you set neither of these parameters, the Agora server automatically sets the banning time duration to 60 minutes, that is, 3600 seconds.</li></ul> |

### Request examples
Test this request in [Postman](https://documenter.getpostman.com/view/6319646/SVSLr9AM#ed57bf68-671c-4a1d-93ed-e545d9901745) or use one of the following code examples:

**Curl**
```bash
curl --request PUT \
    --url https://api.sd-rtn.com/dev/v1/kicking-rule \
    --header 'Accept: application/json' \
    --header 'Authorization: ' \
    --header 'Content-Type: application/json' \
    --data '{
    "appid": "4855xxxxxxxxxxxxxxxxxxxxxxxxeae2",
    "id": 1953,
    "time": 60
  }'
```

**Node.js**
```js
const http = require('http');

const options = {
method: 'PUT',
hostname: 'api.sd-rtn.com',
port: null,
path: '/dev/v1/kicking-rule',
headers: {
  Authorization: '',
  'Content-Type': 'application/json',
  Accept: 'application/json'
}
};

const req = http.request(options, function (res) {
const chunks = [];

res.on('data', function (chunk) {
  chunks.push(chunk);
});

res.on('end', function () {
  const body = Buffer.concat(chunks);
  console.log(body.toString());
});
});

req.write(JSON.stringify({appid: '4855xxxxxxxxxxxxxxxxxxxxxxxxeae2', id: 1953, time: 60}));
req.end();
```

**Python**
```python
import http.client

conn = http.client.HTTPConnection("api.sd-rtn.com")

payload = "{
  "appid": "4855xxxxxxxxxxxxxxxxxxxxxxxxeae2",
  "id": 1953,
  "time": 60
}"

headers = {
  'Authorization': "",
  'Content-Type': "application/json",
  'Accept': "application/json"
}

conn.request("PUT", "/dev/v1/kicking-rule", payload, headers)

res = conn.getresponse()
data = res.read()

print(data.decode("utf-8"))
```


### Response parameters

For details about possible response status codes, see [Response status codes](https://docs-md.agora.io/en/video-calling/channel-management-api/response-status-code.md).

If the status code is not `200`, the request fails. See the `message` field in the response body for the reason for this failure.

If the status code is `200`, the request succeeds, and the response body includes the following parameters:

| Parameter | Type   | Description                                                  |
| :-------- | :----- | :----------------------------------------------------------- |
| `status`  | String | The status of this request. `success` means the request succeeds. |
| `result`  | Object | The result of the update:<ul><li>`id`: String. The rule ID.</li><li>`ts`: String. The UTC time when the rule expires.</li></ul> |

### Response example

The following is a response example for a successful request:

```json
{
  "status": "success",
  "result": {
    "id": 1953,
    "ts": "2018-01-09T08:45:54.545Z"
  }
}
```