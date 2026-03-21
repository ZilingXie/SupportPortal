---
title: Get rule list
description: API reference for getting a list of user banning rules
sidebar_position: 2
platform: android
exported_from: https://docs.agora.io/en/video-calling/channel-management-api/endpoint/ban-user-privileges/get-rule-list
exported_on: '2026-01-20T05:57:36.068544Z'
exported_file: get-rule-list.md
---

[HTML Version](https://docs.agora.io/en/video-calling/channel-management-api/endpoint/ban-user-privileges/get-rule-list)

# Get rule list

This method gets the list of all banning rules.

### Prototype

- Method: `GET`
- Endpoint: `https://api.agora.io/dev/v1/kicking-rule?appid={{APPID}}`

> ℹ️ **Note**
> To maximize the success rate of core functions, create (POST), update (PUT), and delete (DELETE), the success rate and accuracy of the query (GET) method is degraded to a certain extent when the quality of the public network is abnormally low. Some request records may be missing in the returned results of the query (GET). When calling POST to create a rule (`time` is not set to 0), which you need to update or delete later, best practice is to:
> * Save the rule ID returned in the POST request on your server, and rely on this ID for subsequent update and delete operations.
> * To ensure that you can still obtain the rule ID returned in the POST request under poor network connections, set the timeout for the POST request to 20 seconds or higher. Make sure that the timeout is set to no less than 5 seconds.
> * In case the POST request times out or returns a `504` error, use the response of the GET method to obtain the rule ID. If the rule exists, it indicates that the POST request is successful, and you can save the rule ID on your server.

### Request parameters

**Query parameters**

Pass the following query parameters in the request URL:

| Parameter | Type           | Required/Optional |Description                            |
| :------ | :---------------- |:------| :------------------------------ |
| `appid` | String | Required | The App ID of the project. You can get it through one of the following methods:<ul><li>Copy from the [Agora Console](https://console.agora.io/v2).</li><li> Call the [Get all projects](https://docs-md.agora.io/en/interactive-live-streaming/reference/agora-console-rest-api.md) API, and read the value of the `vendor_key` field in the response body.</li></ul> |

**Request header**

The `Content-Type` field in all HTTP request headers is `application/json`. All requests and responses are in JSON format. All request URLs and request bodies are case-sensitive.

The Agora Channel Management RESTful APIs only support HTTPS. Before sending HTTP requests, you must generate a Base64-encoded credential with the **Customer ID** and **Customer Secret** provided by Agora, and pass the credential to the `Authorization` field in the HTTP request header. See [RESTful authentication](https://docs-md.agora.io/en/video-calling/channel-management-api/restful-authentication.md) for details.

### Request examples
Test this request in [Postman](https://documenter.getpostman.com/view/6319646/SVSLr9AM#0640d215-02df-4185-abba-456fd233a7d8) or use one of the following code examples:

**Curl**
```bash
curl --request GET \
  --url https://api.sd-rtn.com/dev/v1/kicking-rule \
  --header 'Accept: application/json' \
  --header 'Authorization: '
```

**Node.js**
```js
const http = require('http');
\const options = {
method: 'GET',
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

conn.request("GET", "/dev/v1/kicking-rule", headers=headers)

res = conn.getresponse()
data = res.read()
print(data.decode("utf-8"))
```


### Response parameters

For details about possible response status codes, see [Response status codes](https://docs-md.agora.io/en/video-calling/channel-management-api/response-status-code.md).

If the status code is not `200`, the request fails. See the `message` field in the response body for the reason for this failure.

If the status code is `200`, the request succeeds, and the response body includes the following parameters:

| Parameter | Type | Description                                               |
| :--------- | :----- | :----------------------------------------------------------- |
| `status`   | String | The status of this request. `success` means the request succeeds. |
| `rules`    | Array | The list of banning rules. This array consists of multiple objects. Each object contains the information on one banning rule and includes the following fields:<ul><li>`id`: Number. The rule ID. If you want to update or delete the rule, you need the rule ID to specify the rule.</li><li>`appid`: String. The App ID of the project.</li><li>`uid`: Number. The user ID.</li><li>`opid`: Number. The operation ID, which can be used to track operation records when troubleshooting.</li><li>`cname`: String. The channel name.</li><li>`ip`: String. The IP address of the user.</li><li>`ts`: String. The UTC time when this rule expires.</li><li>`privileges`: Array. User privileges, including the following values: <ul><li>`join_channel`: String. Bans a user from joining a channel or kicks a user out of a channel.</li><li>`publish_audio`: String. Bans a user from publishing audio.</li><li>`publish_video`: String. Bans a user from publishing video.</li></ul></li></ul> |
| `createAt` | String | The UTC time when this rule is created. |
| `updateAt` | String | The UTC time when this rule is updated. |

### Response example

The following is a response example for a successful request:

```json
{
  "status": "success",
  "rules": [
    {
      "id": 1953,
      "appid": "4855xxxxxxxxxxxxxxxxxxxxxxxxeae2",
      "uid": 589517928,
      "opid": 1406,
      "cname": "11",
      "ip": "192.168.0.1",
      "ts": "2018-01-09T07:23:06.000Z",
      "privileges": [
        "join_channel"
      ],
      "createAt": "2018-01-09T06:23:06.000Z",
      "updateAt": "2018-01-09T14:23:06.000Z"
    }
  ]
}
```