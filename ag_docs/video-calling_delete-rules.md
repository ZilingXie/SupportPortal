---
title: Delete rule
description: API reference for deleting a rule to ban users
sidebar_position: 4
platform: android
exported_from: https://docs.agora.io/en/video-calling/channel-management-api/endpoint/ban-user-privileges/delete-rules
exported_on: '2026-01-20T05:57:35.611907Z'
exported_file: delete-rules.md
---

[HTML Version](https://docs.agora.io/en/video-calling/channel-management-api/endpoint/ban-user-privileges/delete-rules)

# Delete rule

This method deletes a specified banning rule.

### Prototype

- Method: `DELETE`
- Endpoint: `https://api.agora.io/dev/v1/kicking-rule`

### Request parameters

**Request header**

The `Content-Type` field in all HTTP request headers is `application/json`. All requests and responses are in JSON format. All request URLs and request bodies are case-sensitive.

The Agora Channel Management RESTful APIs only support HTTPS. Before sending HTTP requests, you must generate a Base64-encoded credential with the **Customer ID** and **Customer Secret** provided by Agora, and pass the credential to the `Authorization` field in the HTTP request header. See [RESTful authentication](https://docs-md.agora.io/en/video-calling/channel-management-api/restful-authentication.md) for details.

**Request body**

The following parameters are required in the request body:

```json
{
  "appid": "4855xxxxxxxxxxxxxxxxxxxxxxxxeae2",
  "id": 1953
}
```

| Parameter | Type   | Required/Optional | Description                                                  |
| :------ | :-----  |:----- | :---------------------- |
| `appid` | String | Required | The App ID of the project. You can get it through one of the following methods:<ul><li>Copy from the [Agora Console](https://console.agora.io/v2).</li><li> Call the [Get all projects](https://docs-md.agora.io/en/interactive-live-streaming/reference/agora-console-rest-api.md) API, and read the value of the `vendor_key` field in the response body.</li></ul>|
| `id` | Number | Required | The ID of the rule that you want to delete. |

### Request examples
Test this request in [Postman](https://documenter.getpostman.com/view/6319646/SVSLr9AM#af1b1648-86b5-4d15-9fba-fcc1394e30e5) or use one of the following code examples:

**Curl**
```bash
curl --request DELETE \
    --url https://api.sd-rtn.com/dev/v1/kicking-rule \
    --header 'Accept: application/json' \
    --header 'Authorization: '
```

**Node.js**
```js
const http = require('http');

const options = {
method: 'DELETE',
hostname: 'api.sd-rtn.com',
port: null,
path: '/dev/v1/kicking-rule',
headers: {
  Authorization: '',
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

req.end();
```

**Python**
```python
import http.client

conn = http.client.HTTPConnection("api.sd-rtn.com")

headers = {
  'Authorization': "",
  'Accept': "application/json"
}

conn.request("DELETE", "/dev/v1/kicking-rule", headers=headers)

res = conn.getresponse()
data = res.read()

print(data.decode("utf-8"))
```


### Response parameters

For details about possible response status codes, see [Response status codes](https://docs-md.agora.io/en/video-calling/channel-management-api/response-status-code.md).

If the status code is not `200`, the request fails. See the `message` field in the response body for the reason for this failure.

If the status code is `200`, the request succeeds, and the response body includes the following parameters:

| Parameter | Type   | Description                                                  |
| :----------------- | :----- | :----------------------------------------------------------- |
| `status`               | String | The status of this request. `success` means the request succeeds. |
| `id`           | String | The ID of the rule that you want to delete. |

### Response example

The following is a response example for a successful request:

```json
{
  "status": "success",
  "id": 1953
}
```