---
title: Query user list
description: API reference for querying the user list
sidebar_position: 2
platform: android
exported_from: https://docs.agora.io/en/video-calling/channel-management-api/endpoint/query-channel-information/query-user-list
exported_on: '2026-01-20T05:57:38.006219Z'
exported_file: query-user-list.md
---

[HTML Version](https://docs.agora.io/en/video-calling/channel-management-api/endpoint/query-channel-information/query-user-list)

# Query user list

This method gets the list of all users in a specified channel.

### Prototype

- Method: `GET`
- Endpoint: `https://api.agora.io/dev/v1/channel/user/{appid}/{channelName}`

The return list differs based on the channel profile as follows:

- For the `COMMUNICATION` profile, this API returns the list of all users in the channel.
- For the `LIVE_BROADCASTING` profile, this API returns the list of all hosts and audience members in the channel.

> ℹ️ **Note**
> <ul><li>Users in a channel must use the same channel profile; otherwise, the query results may be inaccurate.</li><li>You can synchronize the online channel statistics either by calling this API or by calling the [Query user status API](https://docs-md.agora.io/en/query-user-status.md). This API requires a lower call frequency and has a higher query efficiency. Therefore, Agora recommends using this API to query online channel statistics.</li></ul>

### Request parameters

**Path parameters**

Pass the following path parameters in the request URL:

| Parameter     | Type   | Required/Optional | Description                                                  |
| :------ | :-----  |:----- | :---------------------- |
| `appid` | String | Required |The App ID of the project. You can get it through one of the following methods:<ul><li>Copy from the [Agora Console](https://console.agora.io/v2).</li><li> Call the [Get all projects](https://docs-md.agora.io/en/interactive-live-streaming/reference/agora-console-rest-api.md) API, and read the value of the `vendor_key` field in the response body.</li></ul>|
| `channelName` | String | Required | The channel name. |
| `hosts_only` | String |Optional|If you fill in this parameter, only the host list in the live broadcast use-case will be returned.|

**Request header**

The `Content-Type` field in all HTTP request headers is `application/json`. All requests and responses are in JSON format. All request URLs and request bodies are case-sensitive.

The Agora Channel Management RESTful APIs only support HTTPS. Before sending HTTP requests, you must generate a Base64-encoded credential with the **Customer ID** and **Customer Secret** provided by Agora, and pass the credential to the `Authorization` field in the HTTP request header. See [RESTful authentication](https://docs-md.agora.io/en/video-calling/channel-management-api/restful-authentication.md) for details.

### Request examples
Test this request in [Postman](https://documenter.getpostman.com/view/6319646/SVSLr9AM#85067b69-dbde-4dca-bd54-2413221844cf) or use one of the following code examples:

**Curl**
```bash
curl --request GET \
  --url https://api.sd-rtn.com/dev/v1/channel/user/appid/channelName/hosts_only \
  --header 'Accept: application/json' \
  --header 'Authorization: '
```

**Node.js**
```js
const http = require('http');

const options = {
method: 'GET',
hostname: 'api.sd-rtn.com',
port: null,
path: '/dev/v1/channel/user/appid/channelName/hosts_only',
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
| `success` | Boolean | The state of this request:<li>`true`: Success.</li><li>`false`: Reserved for future use.</li> |
| `data`    | Object  | User information, including the following fields: <ul><li>`channel_exist`: Boolean. Whether the specified channel exists:<ul><li>`true`: The channel exists.</li><li>`false`: The channel does not exist.</li></ul>**Note**: All other fields are not returned when the value of `channel_exist` is `false`.</li><li>`mode`: Number. The channel profile:<ul><li>`1`：The `COMMUNICATION` profile.</li><li>`2`: The `LIVE_BROADCASTING` profile.</li></ul></li><li>`total`: Number. The total number of the users in the channel. This field is returned only when `mode` is `1`.</li><li>`users`: Array. User IDs of all users in the channel. This field is returned only when `mode` is `1`.</li><li>`broadcasters`：Array. User IDs of all hosts in the channel. This field is returned only when `mode` is `2`.</li><li>`audience`: Array. User IDs of the first 10,000 audience members in the channel. This field is returned only when `mode` is `2` and the `hosts_only` parameter is not filled in.</li><li>`audience_total`: Number. The total number of audience members in the channel. This field is returned only when `mode` is `2` and the `hosts_only` parameter is not filled in.</li></ul> |

### Response example

The following is a response example for a successful request:

**In `COMMUNICATION` profile**

```json
{
  "success": true,
  "data": {
    "channel_exist": true,
    "mode": 1,
    "total": 1,
    "users": [
      906218805
    ]
  }
}
```

**In `LIVE_BROADCASTING` profile**

```json
{
    "success": true,
    "data": {
        "channel_exist": true,
        "mode": 2,
        "broadcasters": [
            2206227541,
            2845863044
        ],
        "audience": [
            906219905
        ],
        "audience_total": 1
    }
}
```