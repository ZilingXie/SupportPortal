---
title: Connection status management
description: Test network quality and adjust audio and video settings to optimize
  channel quality
sidebar_position: 2
platform: android
exported_from: https://docs.agora.io/en/video-calling/enhance-call-quality/connection-status-management?platform=android
exported_on: '2026-01-20T05:57:47.937666Z'
exported_file: connection-status-management_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/enhance-call-quality/connection-status-management?platform=android)

# Connection status management

In real-time audio and video implementations, the connection state between the app and Agora SDRTN® changes as the client joins or leaves the channel. The connection may also be interrupted due to network or authentication issues.

This page describes the various channel connection states, reasons for state changes, and how to handle these changes to better manage your users and troubleshoot network faults.

## Understand the tech

The following figure shows the various connection states and how the connection state changes between a user joining and leaving a channel:

![Connection State](https://docs-md.agora.io/images/video-sdk/connection-state-main.svg)

##### Disconnection and reconnection

During the communication process, if the user is disconnected due to network problems, the SDK automatically enables the disconnection and reconnection mechanism. The following figure shows the callbacks received by the local user `UID1` and the remote user `UID2` when:

* A local user joins the channel
* A network exception occurs
* The connection is interrupted
* The user rejoins the channel

![Disconnection Connection](https://docs-md.agora.io/images/video-sdk/connection-state-native.svg)

In the diagram:

- **T0**: The Video SDK receives a `joinChannel` request from `UID1`.

- **T1**: After 200 ms of calling `joinChannel`, `UID1` joins the channel. At the same time, `UID1` receives `onConnectionStateChanged(CONNECTING, CONNECTING)` callback. After successfully joining the channel, `UID1` receives `onConnectionStateChanged(CONNECTED, JOIN_SUCCESS)` and `onJoinChannelSuccess` callbacks.

- **T2**: Due to the transmission delay between networks, `UID2` observes a delay of about 100 milliseconds for `UID1` to join the channel, and at this point `UID2` receives a `onUserJoined` callback.

- **T3**: When `UID1` client's connection deteriorates due to a network issue or some other reason, the Video SDK automatically tries to rejoin the channel.

- **T4**: If `UID1` does not receive any data from the server for 4 consecutive seconds, `UID1` receives the `onConnectionStateChanged(RECONNECTING, INTERRUPTED)` callback. Meanwhile, the Video SDK continues to try to rejoin the channel.

- **T5**: If `UID1` does not receive any data from the server for 10 consecutive seconds after receiving `onConnectionStateChanged(RECONNECTING, INTERRUPTED)`, `UID1` receives the `onConnectionLost` callback. Meanwhile, the Video SDK continues to try to rejoin the channel.

- **T6**: If `UID2` does not receive any data from `UID1` for 20 consecutive seconds, the Video SDK determines that `UID1` is offline. So `UID2` receives the `onUserOffline` callback.

- **T7**: If `UID1` fails to rejoin the channel for 20 consecutive minutes after receiving `onConnectionStateChanged(RECONNECTING, INTERRUPTED)`, the SDK stops retrying. `UID1` receives the `onConnectionStateChanged(FAILED, JOIN_FAILED)` callback and the user must exit and then rejoin the channel.

## Prerequisites
Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) project.

## Implement connection status management


When the connection state changes, Video SDK sends an `onConnectionStateChanged` callback. This section shows you how to use the `onConnectionStateChanged` callback to monitor changes in the connection state.

Use the following code in your `IRtcEngineEventHandler` to log the connection state changes and the reason for the state change.

**Java**
```java
public void onConnectionStateChanged(int state, int reason) {
    super.onConnectionStateChanged(state, reason);
    Log.i(TAG, "onConnectionStateChanged->" + ", state->" + state 
        + ", reason->" + reason);
}
```  

**Kotlin**
```kotlin
override fun onConnectionStateChanged(state: Int, reason: Int) {
    super.onConnectionStateChanged(state, reason)
    Log.i(TAG, "onConnectionStateChanged->, state->$state, reason->$reason")
}
```


The `state` parameter in `onConnectionStateChanged` reports the current connection state. The `reason` parameter indicates why the connection state changed to help you troubleshoot your network. See [State description and troubleshooting](#state-description-and-troubleshooting).

## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Connection states

The app may have the following connection states before joining, during a session, and after leaving a channel:

|Connection state |Description	|
|:-----------------|:-----------|
|**Disconnected** |Initial connection state. Usually occurs: <ul><li>Before calling `joinChannel`.</li><li>After calling `leaveChannel`.</li></ul> |
|**Connecting** |The transient state after calling `joinChannel`. |
|**Connected** |Occurs after the app successfully joins a channel. The SDK also triggers the `onJoinChannelSuccess` callback to report that the local client has joined the channel. At this point, the user can publish or subscribe to the audio and video in the channel. |
|**Reconnecting** |Occurs when the connection is interrupted. The SDK automatically tries to reconnect after an interruption. <ul><li>If the client successfully rejoins the channel, the SDK triggers the `onRejoinChannelSuccess` callback.</li><li>If the channel is not rejoined within 10 seconds, the SDK triggers `onConnectionStateChanged(Reconnecting, Lost)`, and continues trying to rejoin the channel.</li></ul> |
|**Failed** |Connection failed. Occurs when the SDK is unable to join a channel for 20 minutes and stops attempting to reconnect to the channel. In this case, call `leaveChannel` to leave the current channel, and then call `joinChannel` to join the channel again. |

### State description and troubleshooting

The `reason` parameter in `onConnectionStateChanged` describes the reason for the connection state change.

The following table maps the relationships between different connection states and the causes of state change, as well as how to handle the situation when network outages occur:

|Connection state |Description and troubleshooting guide |
|:-----------------|:-----------|
|**Disconnected** |<ul><li>`LEAVE_CHANNEL` (5): The user leaves the channel.</li><li>`INVALID_TOKEN` (8):The token is invalid. Please use a valid token to join the channel.</li></ul> |
|**Connecting** |`CONNECTING` (0): The app is trying to join the Agora channel. |
|**Connected** |`JOIN_SUCCESS` (1): The app has successfully joined the channel. |
|**Reconnecting** |<ul><li>`INTERRUPTED` (2): When the network connection is interrupted, the SDK automatically reconnects to the channel and the connection state continues to change. For details on how the connection state changes during automatic reconnection, see [Disconnection and reconnection](#disconnection-and-reconnection).</li><li>`LOST` (16): The SDK lost connection with the server.</li><li>`SETTING_PROXY_SERVER` (11): The SDK attempts to reconnect because a proxy server is configured.</li><li>`CLIENT_IP_ADDRESS_CHANGED` (13): The client IP address has changed. If this status code occurs multiple times, prompt the user to rejoin the channel after changing networks.</li><li>`KEEP_ALIVE_TIMEOUT` (14): The keep-alive connection between the SDK and the server has timed out, causing the SDK to enter an automatic reconnection state.</li><li>`RENEW_TOKEN` (12): Updating the token has caused a change in the network connection status.</li></ul> |
|**Failed** |<ul><li>`BANNED_BY_SERVER` (3): The user is banned by the server.</li><li>`JOIN_FAILED` (4): The SDK stopped trying to reconnect after continued failed attempts to join the channel for 20 minutes. Call `leaveChannel` to leave the current channel and then call `joinChannel` to rejoin the channel.</li><li>`INVALID_APP_ID` (6): The app ID is invalid. Use a valid app ID to join the channel.</li><li>`INVALID_CHANNEL_NAME` (7): The channel name is invalid. Please check if the channel name contains illegal characters and use a valid channel name to join the channel.</li><li>`TOKEN_EXPIRED` (9): The token has expired. Obtain a new token from the app server, and then call `joinChannel` to rejoin the channel.</li><li>`REJECTED_BY_SERVER` (10): The user is banned by the server. May also occurs under the following circumstances:<ul><li>The app calls `joinChannel` again after the local user has joined the channel.</li><li>The app called `startEchoTest`, but did not call `stopEchoTest` to end the echo test.</li></ul></li></ul> |

### API reference

- [`onConnectionStateChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onconnectionstatechanged)
- [`getConnectionState`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_getconnectionstate)