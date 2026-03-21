---
title: Optimize first-frame rendering
description: Best practices for optimal audio quality.
sidebar_position: 6
platform: android
exported_from: https://docs.agora.io/en/video-calling/best-practices/optimize-frame-rendering?platform=android
exported_on: '2026-01-20T05:57:27.509657Z'
exported_file: optimize-frame-rendering_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/best-practices/optimize-frame-rendering?platform=android)

# Optimize first-frame rendering

First frame output time is the duration between when a user joins a channel and when they first see the remote video. A shorter first frame output time reduces perceived wait time by rendering video more quickly.

This guide describes two best practices to reduce video rendering time in Video Calling.

## Prerequisites

Complete the steps in the [SDK Quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) to build a basic Video Calling app.

## Understand the tech

To reduce video rendering time, Agora provides the following solutions:

* **Preload and initialize before joining the channel**  
  Complete time-consuming operations ahead of time, such as preloading the channel, configuring the rendering view, and enabling accelerated rendering for audio and video frames.

* **Join early, subscribe later**  
  Join the channel in advance but delay subscribing to the audio and video stream. When the user triggers the join operation, subscribe to the host's stream and begin rendering immediately.

The following table compares both solutions:

| Characteristic       | Preload and initialize early   | Join early, subscribe on demand  |
| -------------------- | -------------------------------|--------------------------------- |
| Applicable scenarios | Most audio and video use cases | Scenarios with very high requirements for first frame rendering speed  |
| Core implementation  | Initialize and configure video settings before joining | Join the channel early without subscribing; subscribe only when needed |
| Cost   | Normal billing             | May incur additional channel usage fees    |

The following figure shows the time to output the first frame before optimization and with each solution:

![Optimize video rendering](https://docs-md.agora.io/images/video-sdk/optimize-video-rendering-tech.jpg)

## Implement fast first-frame rendering

This section describes the implementation logic for both solutions.


**Preload and initialize early**
The following figure illustrates the essential steps:

**Sequence diagram for implementation**

![Sequence diagram for optimized video rendering](https://docs-md.agora.io/images/video-sdk/optimize-video-rendering-solution-1.svg)

### Set up a Video SDK instance

Creating and initializing the Video SDK engine takes time. To reduce first-frame display time, Agora recommends initializing the engine when the module is loaded, not when SDK functions are first called.

> ℹ️ **Info**
> Initialize the engine only once. Avoid creating and destroying it multiple times.

```kotlin
class AgoraQuickStartActivity : AppCompatActivity() {
    private var mRtcEngine: RtcEngine? = null
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // Create and initialize the engine during activity creation
        initializeAgoraEngine()
    }
    
    private fun initializeAgoraEngine() {
        try {
            val config = RtcEngineConfig().apply {
                mContext = applicationContext
                mAppId = "Your App ID"
                mChannelProfile = Constants.CHANNEL_PROFILE_LIVE_BROADCASTING
                mEventHandler = mRtcEventHandler // You'll need to define this
            }
            
            mRtcEngine = RtcEngine.create(config)
        } catch (e: Exception) {
            throw RuntimeException("Error initializing RTC engine: \${e.message}")
        }
    }
}
```

### Enable accelerated rendering

Call [`enableInstantMediaRendering`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_enableinstantmediarendering) to reduce the time it takes to render the first video frame and play audio after joining a channel.

- Call this method **before** joining a channel. Ideally, call it right after engine initialization.
- Both host and audience must call this method to benefit from faster rendering.
- To disable this feature, destroy the engine with `release`, then reinitialize it.

```kotlin
// Enable accelerated rendering before joining the channel
mRtcEngine?.enableInstantMediaRendering()
```

### Set a video scenario

Use [`setVideoScenario`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setvideoscenario) to optimize performance for your specific use case. The SDK applies strategies tailored to the selected scenario.

For example, for a one-on-one call, use `APPLICATION_SCENARIO_1V1`.

```kotlin
// Set the video scenario
mRtcEngine?.setVideoScenario(Constants.APPLICATION_SCENARIO_1V1)
```

### Preload a channel

Joining a channel involves acquiring server resources and establishing a connection. Call [`preloadChannel`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_preloadchannel) to handle resource acquisition early and reduce join time.

- The `token`, `channelId`, and `uid` must match the values used in `joinChannel`.
- Call `preloadChannel` as soon as you retrieve the required info.
- Don’t call `joinChannel` immediately after `preloadChannel`.

```kotlin
private fun prepareChannelInfo(): Int {
    uid = getUid()
    channelId = getChannelInfo()
    token = getTokenFromServer(channelId, uid)
    
    // Preload the channel
    mRtcEngine?.preloadChannel(token, channelId, uid)
    
    return 0 // Return success code, adjust as needed
}
```

### Set up the rendering view

Setting the rendering view early ensures the first frame displays properly. If the view is not ready, the first frame might be skipped.

If your app knows the remote user ID (For example, from Signaling), set the view immediately. Otherwise, use the `onUserJoined` callback.

- **Set the remote view early:**

    ```kotlin
    fun onShowChannels(channelId: String, remoteUid: Int) {
            val canvas = VideoCanvas(null, VideoCanvas.RENDER_MODE_FIT, remoteUid)
            mRtcEngine?.setupRemoteVideo(canvas)
        }
    
        fun onEIDUserJoined(uid: Int, elapsed: Int) {
            // Already set - no additional setup needed
        }
    ```

- **Set the view when the user joins:**

    ```kotlin
    // Event handler class
        private val mRtcEventHandler = object : IRtcEngineEventHandler() {
            override fun onUserJoined(uid: Int, elapsed: Int) {
                // Forward to UI logic
                runOnUiThread {
                    onEIDUserJoined(uid, elapsed)
                }
            }
        }
    
        fun onEIDUserJoined(uid: Int, elapsed: Int) {
            val canvas = VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, uid)
            mRtcEngine?.setupRemoteVideo(canvas)
        }
    ```

### Monitor rendering performance

Use [`startMediaRenderingTracing`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_startmediarenderingtracing) to monitor first-frame rendering metrics. Results are reported via [`onVideoRenderingTracingResult`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onvideorenderingtracingresult).

> ℹ️ **Info**
> Call this method when the user initiates joining. For example, on a **Join** button tap. This gives accurate first-frame timing.

```kotlin
private fun onJoinClicked() {
    mRtcEngine?.startMediaRenderingTracing()
    mRtcEngine?.joinChannel(token, channelId, uid, options)
}
```

### Join a channel

Call [`joinChannel`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_joinchannel2) to enter the channel. To speed up first-frame playback, avoid delays like fetching a token in this method.

If you can’t retrieve a token early, consider using a [wildcard token](https://docs-md.agora.io/en/video-calling/token-authentication/deploy-token-server.md).

```kotlin
private fun prepareChannelInfo(): Int {
    uid = getUid()
    channelId = getChannelInfo()
    token = getTokenFromServer(channelId, uid)
    return 0 // Return success code
}

private fun joinChannel(): Int {
    val options = ChannelMediaOptions()
    return mRtcEngine?.joinChannel(token, channelId, uid, options) ?: -1
}
```

### Optimize callback performance

The SDK runs callbacks like `onJoinChannelSuccess` on the same thread. If one callback is slow, it can delay others—including rendering events.

> ℹ️ **Info**
> Don’t block the callback thread with network calls, file I/O, or heavy processing.

#### Best practices

- Avoid complex operations in `onJoinChannelSuccess`.
- Don’t block `onUserJoined` or other rendering-related callbacks.
- Use background threads for heavy logic.

**Join early, subscribe on demand**
The following figure illustrates the essential steps:

**Sequence diagram for implementation**

![Sequence diagram for optimized video rendering](https://docs-md.agora.io/images/video-sdk/optimize-video-rendering-solution-2.svg)

### Set up the rendering view

If you know the host’s user ID before joining the channel, call [`setupRemoteVideoEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#api_irtcengineex_setupremotevideoex) as early as possible to set up the rendering view. This ensures the rendering pipeline is initialized in advance, helping avoid delays in displaying the first decoded frame.

If the host’s user ID is not available beforehand, wait for the `onUserJoined` callback, then call [`setupRemoteVideoEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#api_irtcengineex_setupremotevideoex).

```kotlin
val canvas = VideoCanvas(getView(), VideoCanvas.RENDER_MODE_FIT, farNextChannel.remoteUid)
mRtcEngine?.setupRemoteVideoEx(canvas, connection)
```

### Join a channel without automatically subscribing

Joining a channel typically takes the most time before the first video frame appears. For use cases like fast channel switching, delay subscribing to media streams to speed up rendering:

1. Call [`joinChannelEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#api_irtcengineex_joinchannelex) to join the channel.
2. In [`ChannelMediaOptions`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_channelmediaoptions.html), set `autoSubscribeAudio` and `autoSubscribeVideo` to `false`.
3. Subscribe manually when the user is ready to view content.

```kotlin
// Define connection and event handler
val connection = RtcConnection(channelId, uid)
val eventHandler = mRtcEventHandler

// Set channel media options
val options = ChannelMediaOptions().apply {
    channelProfile = Constants.CHANNEL_PROFILE_LIVE_BROADCASTING
    clientRoleType = Constants.CLIENT_ROLE_AUDIENCE
    autoSubscribeAudio = false
    autoSubscribeVideo = false
}

// Join the channel without subscribing
mRtcEngine?.joinChannelEx(token, connection, options, mRtcEventHandler)
```

### Subscribe to streams and start rendering

When the user chooses to view content:

1. Resume media subscription using [`muteRemoteVideoStreamEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#api_irtcengineex_muteremotevideostreamex) and [`muteRemoteAudioStreamEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#api_irtcengineex_muteremoteaudiostreamex).
2. Call [`startMediaRenderingTracingEx`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineex.html#api_irtcengineex_startmediarenderingtracingex) to log rendering metrics.
3. The SDK reports results in the [`onVideoRenderingTracingResult`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onvideorenderingtracingresult) callback, which you can use for performance analysis.

```kotlin
fun switchToChannel() {
    // Start video rendering tracing
    mRtcEngine?.startMediaRenderingTracingEx(connection)
    
    // Resume remote media subscriptions
    mRtcEngine?.muteRemoteVideoStreamEx(remoteUid, false, connection)
    mRtcEngine?.muteRemoteAudioStreamEx(remoteUid, false, connection)
}
```

## Troubleshooting

Refer to [Slow first-frame rendering of remote video when using the Agora Video SDK](https://docs-md.agora.io/en/help/quality-issues/optimize_video_rendering.md).