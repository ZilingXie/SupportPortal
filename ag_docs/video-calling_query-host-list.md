---
title: Query host list
description: API reference for querying the user list
sidebar_position: 3
platform: android
exported_from: https://docs.agora.io/en/video-calling/channel-management-api/endpoint/query-channel-information/query-host-list
exported_on: '2026-01-20T05:57:37.619585Z'
exported_file: query-host-list.md
---

[HTML Version](https://docs.agora.io/en/video-calling/channel-management-api/endpoint/query-channel-information/query-host-list)

# Query host list

This method gets the list of hosts in the specified channel in the live broadcast use-case.

### Prototype

- Method: `GET`
- Endpoint: `https://api.agora.io/dev/v1/channel/user/{appid}/{channelName}/hosts_only`

This API is only used in the live broadcast profile (`mode` is set to `2`). Users in the same channel must use the same profile. Otherwise, the query results may be inaccurate.

This API and the [query user status](https://docs-md.agora.io/en/query-user-status.md) API can both be used to synchronize the online status of hosts. Compared to the [query user status](https://docs-md.agora.io/en/query-user-status.md) API, this API requires a lower call frequency and has a higher efficiency. Therefore, Agora recommends using this API for this purpose.

### Request parameters

**Path parameters**

Pass the following path parameters in the request URL:

| Parameter     | Type   | Required/Optional | Description                                                  |
| :------ | :-----  |:----- | :---------------------- |
| `appid` | String | Required |The App ID of the project. You can get it through one of the following methods:<ul><li>Copy from the [Agora Console](https://console.agora.io/v2).</li><li> Call the [Get all projects](https://docs-md.agora.io/en/interactive-live-streaming/reference/agora-console-rest-api.md) API, and read the value of the `vendor_key` field in the response body.</li></ul> |
| `channelName` | String | Required | The channel name. |

**Request header**

The `Content-Type` field in all HTTP request headers is `application/json`. All requests and responses are in JSON format. All request URLs and request bodies are case-sensitive.

The Agora Channel Management RESTful APIs only support HTTPS. Before sending HTTP requests, you must generate a Base64-encoded credential with the **Customer ID** and **Customer Secret** provided by Agora, and pass the credential to the `Authorization` field in the HTTP request header. See [RESTful authentication](https://docs-md.agora.io/en/video-calling/channel-management-api/restful-authentication.md) for details.

### Request examples

**Curl**
```bash
curl --request GET \
  --url https://api.sd-rtn.com/dev/v1/channel/user/appid/channelName/hosts_only \
  --header 'Accept: application/json'\ \
  --header 'Authorization: Basic 123'
```

**Node.js**
```js
const http = require('https');

const\ options = {
method: 'GET',
hostname: 'api.sd-rtn.com',
port: null,
path: '/dev/v1/channel/user/appid/channelName/hosts_only',
headers: {
  Accept: 'application/json',
  Authorization: 'Basic 123'
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

conn =\ http.client.HTTPSConnection("api.sd-rtn.com")

headers = {
  'Accept': "application/json",
  'Authorization': "Basic 123"
}

conn.request("GET", "/dev/v1/channel/user/appid/channelName/hosts_only", headers=headers)

res = conn.getresponse()
data = res.read()

print(data.decode("utf-8"))
```


### Response parameters

For details about possible response status codes, see [Response status codes](https://docs-md.agora.io/en/video-calling/channel-management-api/response-status-code.md).

If the status code is not `200`, the request fails. See the `message` field in the response body for the reason for this failure.

If the status code is `200`, the request succeeds, and the response body includes the following parameters:

| Parameter | Type    | Description                                                  |
| :-------- | :------ | :----------------------------------------------------------- |
| `success` | Boolean | The state of this request:<ul><li>`true`: Success.</li><li>`false`: Reserved for future use.</li></ul> |
| `data`    | Object  | User information, including the following fields: <ul><li>`channel_exist`: Boolean. Whether the specified channel exists:<ul><li>`true`: The channel exists.</li><li>`false`: The channel does not exist.</li></ul>**Note**: All other fields are not returned when the value of `channel_exist` is `false`.</li><li>`mode`: Number. The channel profile:<ul><li>`1`：The `COMMUNICATION` profile.</li><li>`2`: The `LIVE_BROADCASTING` profile.</li></ul></li><li>`broadcasters`：Array. User IDs of all hosts in the channel. This field is returned only when `mode` is `2`.</li></ul> |

### Response example

The following is a response example for a successful request:

```json
{
  "success": true,
  "data": {
    "channel_exist": true,
    "mode": 2,
    "broadcasters": [
      574332,
      1347839
    ]
  }
}
```