---
title: Query channel list
description: API reference for querying the channel list
sidebar_position: 4
platform: android
exported_from: https://docs.agora.io/en/video-calling/channel-management-api/endpoint/query-channel-information/query-channel-list
exported_on: '2026-01-20T05:57:37.242789Z'
exported_file: query-channel-list.md
---

[HTML Version](https://docs.agora.io/en/video-calling/channel-management-api/endpoint/query-channel-information/query-channel-list)

# Query channel list

This method gets the list of all channels under a specified project.

### Prototype

- Method: `GET`
- Endpoint: `https://api.agora.io/dev/v1/channel/{appid}`

This API gets the channel list by page. In the request URL, you can specify the page number and the number of channels shown on the page. A successful request returns the channel list of the specified page according to the set `page_size`.

> ℹ️ **Note**
> If the number of users in a channel changes frequently, the query results may be inaccurate. The following situations may occur: <ul><li>A channel appears repeatedly in different pages.</li><li>A channel does not appear in any page.</li></ul>

### Request parameters

**Path parameters**

Pass the following path parameters in the request URL:

| Parameter   | Type   | Required/Optional | Description                                                  |
| :------ | :-----  |:----- | :---------------------- |
| `appid` | String | Required | The App ID of the project. You can get it through one of the following methods:<ul><li>Copy from the [Agora Console](https://console.agora.io/v2).</li><li> Call the [Get all projects](https://docs-md.agora.io/en/interactive-live-streaming/reference/agora-console-rest-api.md) API, and read the value of the `vendor_key` field in the response body.</li></ul>|

**Query parameters**

Pass the following query parameters in the request URL:

| Parameter   | Type   | Required/Optional | Description                                                  |
| :------ | :-----  |:----- | :---------------------- |
| `page_no` | Number | Optional | The page number that you want to query. The default value is 0, that is, the first page. **Note**: The value of `page_no` cannot exceed (the total number of channels/the value of `page_size` - 1); otherwise, the specified page does not contain any channel. |
| `page_size` | Number | Optional | The number of channels on a page. The value range is [1,500], and the default value is 100. |

**Request header**

The `Content-Type` field in all HTTP request headers is `application/json`. All requests and responses are in JSON format. All request URLs and request bodies are case-sensitive.

The Agora Channel Management RESTful APIs only support HTTPS. Before sending HTTP requests, you must generate a Base64-encoded credential with the **Customer ID** and **Customer Secret** provided by Agora, and pass the credential to the `Authorization` field in the HTTP request header. See [RESTful authentication](https://docs-md.agora.io/en/video-calling/channel-management-api/restful-authentication.md) for details.

### Request examples
Test this request in [Postman](https://documenter.getpostman.com/view/6319646/SVSLr9AM#080ffa91-0c31-42ab-9177-7942f8691ea2) or use one of the following code examples:

**Curl**
```bash
curl --request GET \
  --url https://api.sd-rtn.com/dev/v1/channel/appid \
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
path: '/dev/v1/channel/appid',
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

conn.request("GET", "/dev/v1/channel/appid", headers=headers)

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
| `success` | Boolean | The state of this request: <ul><li>`true`: Success. </li><li>`false`: Reserved for future use.</li></ul> |
| `data`    | Object  | Channel statistics, including the following fields: <ul><li>`channels`: Array. The list of channels. This array contains multiple objects. Each object shows the information on a channel and includes the following fields:<ul><li>`channel_name`: String. The channel name. </li><li>`user_count`: Number. The total number of users in the channel. </li></ul>**Note**: If the specified page does not contain any channel, this field is empty.</li><li>`total_size`: Number. The total number of channels under the specified project.</li></ul> |

### Response example

The following is a response example for a successful request:

```json
{
  "success": true,
  "data": {
    "channels": [
      {
        "channel_name": "lkj144",
        "user_count": 3
      }
    ],
    "total_size": 1
  }
}
```