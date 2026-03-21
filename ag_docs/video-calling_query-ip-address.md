---
title: Query notification service IP address
description: API reference for querying the Notifications IP addresses
sidebar_position: 1
platform: android
exported_from: https://docs.agora.io/en/video-calling/channel-management-api/endpoint/message-notification-service/query-ip-address
exported_on: '2026-01-20T05:57:36.844832Z'
exported_file: query-ip-address.md
---

[HTML Version](https://docs.agora.io/en/video-calling/channel-management-api/endpoint/message-notification-service/query-ip-address)

# Query notification service IP address

When using Agora real-time audio and video, you can use the Notifications service to receive channel events.

After activating the service, when a channel event occurs, the Notifications server will deliver the event notification to your message receiving server.

If your server is behind a firewall, you need to call the IP address query API to retrieve the IP addresses of Notifications and configure your firewall to trust all these IP addresses.

Agora occasionally adjusts the Notifications IP addresses. Best practice is to call this endpoint at least every 24 hours and automatically update the firewall configuration.

### Prototype

* Method: `GET`
* Endpoint: `https://api.agora.io/v2/ncs/ip`

### Request parameters

**Request header**

The `Content-Type` field in all HTTP request headers is `application/json`. All requests and responses are in JSON format. All request URLs and request bodies are case-sensitive.

The Agora Channel Management RESTful APIs only support HTTPS. Before sending HTTP requests, you must generate a Base64-encoded credential with the **Customer ID** and **Customer Secret** provided by Agora, and pass the credential to the `Authorization` field in the HTTP request header. See [RESTful authentication](https://docs-md.agora.io/en/video-calling/channel-management-api/restful-authentication.md) for details.

### Request examples

Use one of the following code examples to test this request:

**Curl**
```bash
curl --request GET \
  --url https://api.sd-rtn.com/v2/ncs/ip \
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
path: '/v2/ncs/ip',
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

conn.request("GET", "/v2/ncs/ip", headers=headers)

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
| `data`    | Object  |  <ul><li>`service`<ul><li>`hosts` <ul><li>`primaryIP`: The IP address of the Notifications server. When you receive a response, you need to add the IP address (or list of IP addresses) from this field to the whitelist.</li></ul></li></ul></li></ul> |

### Response example

```json
{
  "data": {
    "service": {
      "hosts": [
        {
          "primaryIP": "xxx.xxx.xxx.xxx"
        },
        {
          "primaryIP": "xxx.xxx.xxx.xxx"
        }
      ]
    }
  }
}
```