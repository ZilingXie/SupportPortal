---
title: Join multiple channels
description: Broadcast or subscribe to multiple channels.
sidebar_position: 12
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/join-multiple-channels?platform=android
exported_on: '2026-01-20T05:56:37.781466Z'
exported_file: join-multiple-channels_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/join-multiple-channels?platform=android)

# Join multiple channels

Agora Video SDK enables you to simultaneously join multiple channels. This capability allows you to receive and publish audio and video streams across multiple channels concurrently.

## Understand the tech

Video SDK's multi-channel functionality is based on two key components: 

* `RtcConnection`

    The `RtcConnection` object identifies a connection. It contains the following information:

    - Channel name
    - User ID of the local user

    You create multiple `RtcConnection` objects, each with a different channel name and user ID. Each `RtcConnection` instance can independently publish multiple audio streams and a single video stream.

* `RtcEngineEx` 

    The class contains methods tailored for interacting with a designated `RtcConnection` object.

    To join multiple channels, you call `joinChannelEx` method in the `RtcEngineEx` class multiple times, using a different `RtcConnection` instance each time. 

When joining multiple channels:

- Ensure that the user ID for each `RtcConnection` object is unique and nonzero.
- Configure publishing and subscribing options for the `RtcConnection` object in `joinChannelEx`.

- Pass the `IRtcEngineEventHandler` object to the `eventHandler` parameter when calling the `joinChannelEx` method to receive multiple channel-related event notifications.

## Prerequisites
Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project. 

## Implementation 

This section explains how to join a second channel as a host after you have already joined the first channel.


1. Declare variables for `RtcEngineEx` and `RtcConnection` objects.

**Java**
```java
private RtcEngineEx engine;
private RtcConnection rtcConnection2 = new RtcConnection();
```

**Kotlin**
```kotlin
private lateinit var engine: RtcEngineEx
private val rtcConnection2 = RtcConnection()
```


1. Initialize the engine instance.

**Java**
```java
engine = (RtcEngineEx) RtcEngine.create(config);
```

**Kotlin**
```kotlin
engine = RtcEngine.create(config) as RtcEngineEx
```


1. Join the channel using a random user ID.

**Java**
```java
private boolean joinSecondChannel() {
     ChannelMediaOptions option = new ChannelMediaOptions();
     mediaOptions.autoSubscribeAudio = true;
     mediaOptions.autoSubscribeVideo = true;
     rtcConnection2.channelId = "channel-2";
     rtcConnection2.localUid = new Random().nextInt(512)+512;
     int ret = engine.joinChannelEx("your token", rtcConnection2, mediaOptions, 
         iRtcEngineEventHandler2);
     return (ret == 0);
 }
```

**Kotlin**
```kotlin
private fun joinSecondChannel(): Boolean {
    val mediaOptions = ChannelMediaOptions().apply {
        autoSubscribeAudio = true
        autoSubscribeVideo = true
    }
    rtcConnection2.channelId = "channel-2"
    rtcConnection2.localUid = (Random().nextInt(512) + 512)
    val ret = engine.joinChannelEx("your token", rtcConnection2, mediaOptions, iRtcEngineEventHandler2)
    return ret == 0
}
```


1. Listen for events in `rtcConnection2` and set up the remote video in the `onUserJoined` callback.

**Java**
```java
private final IRtcEngineEventHandler iRtcEngineEventHandler2 = new IRtcEngineEventHandler() {
    @Override
    public void onJoinChannelSuccess(String channel, int uid, int elapsed) {
        Log.i(TAG, String.format("channel2 onJoinChannelSuccess channel %s uid %d", channel2, uid));
        showLongToast(String.format("onJoinChannelSuccess channel %s uid %d", channel2, uid));
    }
    @Override
    public void onUserJoined(int uid, int elapsed) {
        Log.i(TAG, "channel2 onUserJoined->" + uid);
        showLongToast(String.format("user %d joined!", uid));
        Context context = getContext();
        if (context == null) {
            return;
        }
        handler.post(() ->
        {
            // Display the remote video stream
            SurfaceView surfaceView = null;
            if (fl_remote2.getChildCount() > 0) {
                fl_remote2.removeAllViews();
            }
            // Create the rendering view through RtcEngine
            surfaceView = new SurfaceView(context);
            surfaceView.setZOrderMediaOverlay(true);
            // Add the view to the remote container
            fl_remote2.addView(surfaceView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

            // Set the remote view
            engine.setupRemoteVideoEx(new VideoCanvas(surfaceView, RENDER_MODE_FIT, uid), rtcConnection2);
        });
    }
};
```

**Kotlin**
```kotlin
private val iRtcEngineEventHandler2 = object : IRtcEngineEventHandler() {
    override fun onJoinChannelSuccess(channel: String?, uid: Int, elapsed: Int) {
        Log.i(TAG, String.format("channel2 onJoinChannelSuccess channel %s uid %d", channel2, uid))
        showLongToast(String.format("onJoinChannelSuccess channel %s uid %d", channel2, uid))
    }

    override fun onUserJoined(uid: Int, elapsed: Int) {
        Log.i(TAG, "channel2 onUserJoined->$uid")
        showLongToast("user $uid joined!")

        val context = context
        if (context == null) {
            return
        }

        handler.post {
            // Display the remote video stream
            var surfaceView: SurfaceView? = null
            if (fl_remote2.childCount > 0) {
                fl_remote2.removeAllViews()
            }
            // Create the rendering view through RtcEngine
            surfaceView = SurfaceView(context)
            surfaceView?.zOrderMediaOverlay = true
            // Add the view to the remote container
            fl_remote2.addView(surfaceView, FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

            // Set the remote view
            engine.setupRemoteVideoEx(VideoCanvas(surfaceView, RENDER_MODE_FIT, uid), rtcConnection2)
        }
    }
}
```


## Reference
This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Sample project

Agora provides the [JoinMultipleChannels](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/JoinMultipleChannel.java) open-source sample project for your reference. Download the project or view the source code for a more detailed example.

### API reference

- [`RtcEngineEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#class_irtcengineex)
- [`RtcConnection`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_rtcconnection.html)
- [`joinChannelEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#api_irtcengineex_joinchannelex)
- [`setupRemoteVideoEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#api_irtcengineex_setupremotevideoex)