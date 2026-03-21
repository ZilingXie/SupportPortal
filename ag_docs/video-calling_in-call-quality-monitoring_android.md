---
title: In-call quality monitoring
description: ''
sidebar_position: 5
platform: android
exported_from: https://docs.agora.io/en/video-calling/enhance-call-quality/in-call-quality-monitoring?platform=android
exported_on: '2026-01-20T05:57:52.171351Z'
exported_file: in-call-quality-monitoring_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/enhance-call-quality/in-call-quality-monitoring?platform=android)

# In-call quality monitoring

During a call, Video SDK triggers callbacks related to the video calling quality. These callbacks enable you to monitor your users' experience, troubleshoot issues, and optimize their overall experience

## Understand the tech

After a user joins a channel, Video SDK triggers a series of callbacks every 2 seconds, reporting information such as uplink and downlink network quality, real-time interaction statistics, and statistics of local and remote audio and video streams.

When there is a change in the audio or video state of a user, Video SDK triggers a callback to report the latest state and the reason for the change. The following figure shows the audio transmission process between app clients:

**Audio transmission process**

![Audio transmission process](https://docs-md.agora.io/images/video-sdk/in-call-quality.svg)

To monitor the call quality, Agora provides the following call quality notifications:

**Network quality**

The network quality callback provides insight into the uplink and downlink last mile network quality for each participant in the channel. Last mile refers to the network from your device to Agora server. The [Network quality scores](#network-quality-score) are calculated based on factors such as sending or receiving bitrate, network packet loss rate, round-trip delay, and network jitter.

**Statistics**

The statistics callback, triggered every 2 seconds, reports key metrics such as call duration, the number of participants, system CPU usage, and app CPU usage.

**Audio quality**

Callbacks related to audio quality cover both local and remote audio streams. You monitor statistics and status changes, to gain insights into the quality of audio streams and any related reasons for status changes.

**Video quality**

Video quality callbacks provide information on both local and remote video streams. You receive statistics and status change notifications, that enable you to understand the quality of video streams and any related reasons for status changes.

## Prerequisites
Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implement in-call quality monitoring


In `IRtcEngineEventHandler`, implement the following real-time interaction quality statistics callbacks and audio or video state monitoring callbacks to understand user interaction experience:

- `onNetworkQuality`: Reports uplink and downlink last mile network quality.
- `onRtcStats`: Reports real-time interaction statistics.
- `onLocalAudioStats`: Reports statistics for the sent audio stream.
- `onLocalAudioStateChanged`: Reports local audio stream state changes.
- `onRemoteAudioStats`: Reports statistics for the received remote audio stream.
- `onRemoteAudioStateChanged`: Reports remote audio stream state changes.
- `onLocalVideoStats`: Reports statistics for the sent video stream.
- `onLocalVideoStateChanged`: Reports local video stream state changes.
- `onRemoteVideoStats`: Reports statistics for the received remote video stream.
- `onRemoteVideoStateChanged`: Reports remote video stream state changes.

In your app, add the following code:

**Java**
```java
// Example implementation in Java
private final IRtcEngineEventHandler iRtcEngineEventHandler = new IRtcEngineEventHandler() {
    // Implement the onNetworkQuality callback
    @Override
    public void onNetworkQuality(int uid, int txQuality, int rxQuality) {
        Log.i(TAG, "onNetworkQuality->" + "UID: " + uid + ", TX Quality: " + txQuality + ", RX Quality: " + rxQuality);
    }

    // Implement the onLocalAudioStateChanged callback
    @Override
    public void onLocalAudioStateChanged(int state, int error) {
        super.onLocalAudioStateChanged(state, error);
        Log.i(TAG, "onLocalAudioStateChanged->" + "State: " + state + ", Error: " + error);
    }

    // Implement the onRemoteAudioStateChanged callback
    @Override
    public void onRemoteAudioStateChanged(int uid, int state, int reason, int elapsed) {
        super.onRemoteAudioStateChanged(uid, state, reason, elapsed);
        Log.i(TAG, "onRemoteAudioStateChanged->" + "UID: " + uid + ", State: " + state + ", Reason: " + reason + ", Elapsed: " + elapsed);
    }

    // Implement the onLocalVideoStateChanged callback
    @Override
    public void onLocalVideoStateChanged(Constants.VideoSourceType source, int state, int error) {
        super.onLocalVideoStateChanged(source, state, error);
        Log.i(TAG, "onLocalVideoStateChanged->" + "State: " + state + ", Error: " + error);
    }

    // Implement the onRemoteVideoStateChanged callback
    @Override
    public void onRemoteVideoStateChanged(int uid, int remoteVideoState, int reason, int elapsed) {
        super.onRemoteVideoStateChanged(uid, remoteVideoState, reason, elapsed);
        Log.i(TAG, "onRemoteVideoStateChanged->" + "UID: " + uid + ", State: " + remoteVideoState + ", Reason: " + reason + ", Elapsed: " + elapsed);
    }

    // Implement the onRemoteAudioStats callback
    @Override
    public void onRemoteAudioStats(RemoteAudioStats remoteAudioStats) {
        Log.i(TAG, "onRemoteAudioStats->" + "Received bitrate: " + remoteAudioStats.receivedBitrate);
    }

    // Implement the onLocalAudioStats callback
    @Override
    public void onLocalAudioStats(LocalAudioStats localAudioStats) {
        Log.i(TAG, "onLocalAudioStats->" + "Network transport delay: " + localAudioStats.networkTransportDelay);
    }

    // Implement the onRemoteVideoStats callback
    @Override
    public void onRemoteVideoStats(RemoteVideoStats remoteVideoStats) {
        Log.i(TAG, "onRemoteVideoStats->" + "Received bitrate: " + remoteVideoStats.receivedBitrate);
    }

    // Implement the onLocalVideoStats callback
    @Override
    public void onLocalVideoStats(LocalVideoStats localVideoStats) {
        Log.i(TAG, "onLocalVideoStats->" + "Sent frame rate: " + localVideoStats.sentFrameRate);
        // Log other specific information as needed
    }

    // Implement the onRtcStats callback
    @Override
    public void onRtcStats(RtcStats rtcStats) {
        Log.i(TAG, "onRtcStats->" + "User count: " + rtcStats.userCount + ", Packet loss rate: " + rtcStats.rxPacketLossRate);
    }
};
```  

**Kotlin**
```kotlin
// Example implementation in Kotlin
private val iRtcEngineEventHandler = object : IRtcEngineEventHandler() {
    // Implement the onNetworkQuality callback
    override fun onNetworkQuality(uid: Int, txQuality: Int, rxQuality: Int) {
        Log.i(TAG, "onNetworkQuality-> UID: $uid, TX Quality: $txQuality, RX Quality: $rxQuality")
    }

    // Implement the onLocalAudioStateChanged callback
    override fun onLocalAudioStateChanged(state: Int, error: Int) {
        super.onLocalAudioStateChanged(state, error)
        Log.i(TAG, "onLocalAudioStateChanged-> State: $state, Error: $error")
    }

    // Implement the onRemoteAudioStateChanged callback
    override fun onRemoteAudioStateChanged(uid: Int, state: Int, reason: Int, elapsed: Int) {
        super.onRemoteAudioStateChanged(uid, state, reason, elapsed)
        Log.i(TAG, "onRemoteAudioStateChanged-> UID: $uid, State: $state, Reason: $reason, Elapsed: $elapsed")
    }

    // Implement the onLocalVideoStateChanged callback
    override fun onLocalVideoStateChanged(source: Constants.VideoSourceType, state: Int, error: Int) {
        super.onLocalVideoStateChanged(source, state, error)
        Log.i(TAG, "onLocalVideoStateChanged-> State: $state, Error: $error")
    }

    // Implement the onRemoteVideoStateChanged callback
    override fun onRemoteVideoStateChanged(uid: Int, remoteVideoState: Int, reason: Int, elapsed: Int) {
        super.onRemoteVideoStateChanged(uid, remoteVideoState, reason, elapsed)
        Log.i(TAG, "onRemoteVideoStateChanged-> UID: $uid, State: $remoteVideoState, Reason: $reason, Elapsed: $elapsed")
    }

    // Implement the onRemoteAudioStats callback
    override fun onRemoteAudioStats(remoteAudioStats: RemoteAudioStats) {
        Log.i(TAG, "onRemoteAudioStats-> Received bitrate: \${remoteAudioStats.receivedBitrate}")
    }

    // Implement the onLocalAudioStats callback
    override fun onLocalAudioStats(localAudioStats: LocalAudioStats) {
        Log.i(TAG, "onLocalAudioStats-> Network transport delay: \${localAudioStats.networkTransportDelay}")
    }

    // Implement the onRemoteVideoStats callback
    override fun onRemoteVideoStats(remoteVideoStats: RemoteVideoStats) {
        Log.i(TAG, "onRemoteVideoStats-> Received bitrate: \${remoteVideoStats.receivedBitrate}")
    }

    // Implement the onLocalVideoStats callback
    override fun onLocalVideoStats(localVideoStats: LocalVideoStats) {
        Log.i(TAG, "onLocalVideoStats-> Sent frame rate: \${localVideoStats.sentFrameRate}")
        // Log other specific information as needed
    }

    // Implement the onRtcStats callback
    override fun onRtcStats(rtcStats: RtcStats) {
        Log.i(TAG, "onRtcStats-> User count: \${rtcStats.userCount}, Packet loss rate: \${rtcStats.rxPacketLossRate}")
    }
}
```


## Reference

### Network quality score

| Value | Enumeration | Description |
|:-----:|:------------|:------------|
| 0 | `QUALITY_UNKNOWN` | Network quality is unknown. |
| 1 | `QUALITY_EXCELLENT` | The network quality is excellent. |
| 2 | `QUALITY_GOOD` | The network quality is good, but the bitrate may be slightly lower than excellent. |
| 3 | `QUALITY_POOR` | The network quality is average, and users may experience a slight decrease in call quality. |
| 4 | `QUALITY_BAD` | The network quality is poor, and users may be unable to maintain smooth calls. |
| 5 | `QUALITY_VBAD` | The network quality is extremely poor, making it almost impossible to make calls. |
| 6 | `QUALITY_DOWN` | The network connection is interrupted, and the user is completely unable to make calls. |
| 8 | `QUALITY_DETECTING` | Last-mile network quality detection is in progress. |

 ### API reference

- [`onNetworkQuality`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onnetworkquality)

- [`onRemoteAudioStateChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onremoteaudiostatechanged)

- [`onRemoteVideoStateChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onremotevideostatechanged)

- [`onLocalAudioStateChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onlocalaudiostatechanged)

- [`onLocalVideoStateChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onlocalvideostatechanged)

- [`onRemoteAudioStats`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onremoteaudiostats)

- [`onRemoteVideoStats`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onremotevideostats)

- [`onLocalAudioStats`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onlocalaudiostats)

- [`onLocalVideoStats`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onlocalvideostats)

- [`onRtcStats`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onrtcstats)