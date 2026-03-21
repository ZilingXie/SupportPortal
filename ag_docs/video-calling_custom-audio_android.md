---
title: Custom audio source
description: Integrate a custom video or audio capture into your client
sidebar_position: 3
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/custom-audio?platform=android
exported_on: '2026-01-20T05:56:27.082308Z'
exported_file: custom-audio_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/custom-audio?platform=android)

# Custom audio source

The default audio module of Video SDK meets the need of using basic audio functions in your app. For adding advanced audio functions, Video SDK supports using custom audio sources and custom audio rendering modules.

Video SDK uses the basic audio module on the device your app runs on by default. However, there are certain use-cases where you want to integrate a custom audio source into your app, such as:

* Your app has its own audio module.
* You need to process the captured audio with a pre-processing library for audio enhancement.
* You need flexible device resource allocation to avoid conflicts with other services.

This page shows you how to capture and render audio from custom sources.

## Understand the tech

To set an external audio source, you configure the Agora Engine before joining a channel. To manage the capture and processing of audio frames, you use methods from outside the Video SDK that are specific to your custom source. Video SDK enables you to push processed audio data to the subscribers in a channel.

#### Capture custom audio
The following figure illustrates the process of custom audio capture.

![Audio data transmission](https://docs-md.agora.io/images/video-sdk/audio-data-transmission.svg)

- You implement the capture module using external methods provided by the SDK.

- You call `pushExternalAudioFrame` to send the captured audio frames to the SDK.

#### Render custom audio

The following figure illustrates the process of custom audio rendering.

![Audio Data Transmission](https://docs-md.agora.io/images/video-sdk/custom-audio-rendering-sdk.svg)

- You implement the rendering module using external methods provided by the SDK.

- You call `pullPlaybackAudioFrame` to retrieve the audio data sent by remote users.

## Prerequisites
Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implementation


This section shows you how to implement custom audio capture and render audio from a custom source.

### Custom audio capture

Refer to the following call sequence diagram to implement custom audio capture in your app:

**Custom audio capture process**

![Custom audio capture](https://docs-md.agora.io/images/video-sdk/custom-audio-capture-with-custom-track.svg)

Follow these steps to implement custom audio capture in your project:

1. After initializing `RtcEngine`, call `createCustomAudioTrack` to create a custom audio track and obtain the audio track ID.

    **Java**
    ```java
    AudioTrackConfig config = new AudioTrackConfig();
       config.enableLocalPlayback = false;
       customAudioTrack = engine.createCustomAudioTrack(Constants.AudioTrackType.AUDIO_TRACK_MIXABLE, config);
    ```

    **Kotlin**
    ```kotlin
    val config = AudioTrackConfig().apply {
            enableLocalPlayback = false
       }
       customAudioTrack = engine.createCustomAudioTrack(Constants.AudioTrackType.AUDIO_TRACK_MIXABLE, config)
    ```


2. Call `joinChannel` to join the channel. In `ChannelMediaOptions`, set `publishCustomAudioTrackId` to the audio track ID obtained in step 1, and set `publishCustomAudioTrack` to `true` to publish the custom audio track.

    > ℹ️ **Information**
    > To use `enableCustomAudioLocalPlayback` for local playback of an external audio source, or to adjust the volume of a custom audio track with `adjustCustomAudioPlayoutVolume`, set `enableAudioRecordingOrPlayout` to `true` in `ChannelMediaOptions`.

    **Java**
    ```java
    ChannelMediaOptions option = new ChannelMediaOptions();
       option.clientRoleType = Constants.CLIENT_ROLE_BROADCASTER;
       option.autoSubscribeAudio = true;
       option.autoSubscribeVideo = true;
       // In the audio self-collection use-case, the audio collected by the microphone is not published
       option.publishMicrophoneTrack = false;
       // Publish the custom audio track
       publishCustomAudioTrack = true
       // Set the custom audio track ID
       publishCustomAudioTrackId = customAudioTrack
    
       // Join the channel
       val res = engine.joinChannel(accessToken, channelId, 0, option)
    ```

    **Kotlin**
    ```kotlin
    val option = ChannelMediaOptions().apply {
            clientRoleType = Constants.CLIENT_ROLE_BROADCASTER
            autoSubscribeAudio = true
            autoSubscribeVideo = true
            // In the audio self-collection use-case, the audio collected by the microphone is not published
            publishMicrophoneTrack = false
            // Publish the custom audio track
            publishCustomAudioTrack = true
            // Set the custom audio track ID
            publishCustomAudioTrackId = customAudioTrack
        }
    
        // Join the channel
        val res = engine.joinChannel(accessToken, channelId, 0, option)
    ```


3. Agora provides the [AudioFileReader.java](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/utils/AudioFileReader.java) sample to demonstrate how to read and publish PCM-format audio data from a local file. In a production environment, you create a custom audio acquisition module based on your business needs.

4. Call `pushExternalAudioFrame` to send the captured audio frame to the SDK through the custom audio track. Ensure that the `trackId` matches the audio track ID you obtained by calling `createCustomAudioTrack`. Set `sampleRate`, `channels`, and `bytesPerSample` to define the sampling rate, number of channels, and bytes per sample of the external audio frame.

    > ℹ️ **Information**
    > For audio and video synchronization, Agora recommends calling `getCurrentMonotonicTimeInMs` to get the system’s current monotonic time and setting the `timestamp` accordingly.

    **Java**
    ```java
    audioPushingHelper = new AudioFileReader(requireContext(), (buffer, timestamp) -> {
            if (joined && engine != null && customAudioTrack != -1) {
                // Push external audio frames to SDK
                int ret = engine.pushExternalAudioFrame(buffer, timestamp,
                        AudioFileReader.SAMPLE_RATE,
                        AudioFileReader.SAMPLE_NUM_OF_CHANNEL,
                        Constants.BytesPerSample.TWO_BYTES_PER_SAMPLE,
                        customAudioTrack);
                Log.i(TAG, "pushExternalAudioFrame times:" + (++\pushTimes) + ", ret=" + ret);
            }
       });
    ```

    **Kotlin**
    ```kotlin
    audioPushingHelper = AudioFileReader(requireContext()) { buffer, timestamp ->
            if (joined && engine != null && customAudioTrack != -1) {
                // Push external audio frames to SDK
                val ret = engine.pushExternalAudioFrame(
                    buffer, timestamp,
                    AudioFileReader.SAMPLE_RATE,
                    AudioFileReader.SAMPLE_NUM_OF_CHANNEL,
                    Constants.BytesPerSample.TWO_BYTES_PER_SAMPLE,
                    customAudioTrack
                )
                Log.i(TAG, "pushExternalAudioFrame times: \${++pushTimes\}, ret=$ret")
            }
       }
    ```


5. To stop publishing custom audio, call `destroyCustomAudioTrack` to destroy the custom audio track.

    **Java**
    ```java
    // Destroy the custom audio track
       engine.destroyCustomAudioTrack(customAudioTrack);
    ```

    **Kotlin**
    ```kotlin
    // Destroy the custom audio track
       engine.destroyCustomAudioTrack(customAudioTrack)
    ```


### Custom audio rendering

This section shows you how to implement custom audio rendering. Refer to the following call sequence diagram to implement custom audio rendering in your app:

**Custom audio rendering workflow**

![Custom Audio Rendering Workflow](https://docs-md.agora.io/images/video-sdk/custom-audio-render.svg)

To implement custom audio rendering, use the following methods:

1. Before calling `joinChannel`, use `setExternalAudioSink` to enable and configure custom audio rendering.

    **Java**
    ```java
    rtcEngine.setExternalAudioSink(
            true,      // Enable custom audio rendering
            44100,     // Sampling rate (Hz). Set this value to 16000, 32000, 441000, or 48000
            1          // Number of channels for the custom audio source. Set this value to 1 or 2
       );
    ```

    **Kotlin**
    ```kotlin
    rtcEngine.setExternalAudioSink(
            true,      // Enable custom audio rendering
            44100,     // Sampling rate (Hz). Set this value to 16000, 32000, 441000, or 48000
            1          // Number of channels for the custom audio source. Set this value to 1 or 2
       )
    ```


2. After joining the channel, call `pullPlaybackAudioFrame` to get audio data sent by remote users. Use your own audio renderer to process the audio data and then play the rendered data.

    **Java**
    ```java
    private class FileThread implements Runnable {
    
            @Override
            public void run() {
                while (mPull) {
                    int lengthInByte = 48000 / 1000 * 2 * 1 * 10;
                    ByteBuffer frame = ByteBuffer.allocateDirect(lengthInByte);
                    int ret = engine.pullPlaybackAudioFrame(frame, lengthInByte);
                    byte[] data = new byte[frame.remaining()];
                    frame.get(data, 0, data.length);
                    // Write to a local file or render using a player
                    FileIOUtils.writeFileFromBytesByChannel("/sdcard/agora/pull_48k.pcm", data, true, true);
                    try {
                        Thread.sleep(10);
                    } catch (InterruptedException e) {
                        e.printStackTrace();
                    }
                }
            }
       }
    ```

    **Kotlin**
    ```kotlin
    private class FileThread : Runnable {
    
            override fun run() {
                while (mPull) {
                    val lengthInByte = 48000 / 1000 * 2 * 1 * 10
                    val frame = ByteBuffer.allocateDirect(lengthInByte)
                    val ret = engine.pullPlaybackAudioFrame(frame, lengthInByte)
                    val data = ByteArray(frame.remaining())
                    frame.get(data, 0, data.size)
                    // Write to a local file or render using a player
                    FileIOUtils.writeFileFromBytesByChannel("/sdcard/agora/pull_48k.pcm", data, true, true)
                    try {
                        Thread.sleep(10)
                    } catch (e: InterruptedException) {
                        e.printStackTrace()
                    }
                }
            }
       }
    ```


### Using raw audio data callback

This section explains how to implement custom audio rendering.

To retrieve audio data for playback, implement collection and processing of raw audio data. Refer to [Raw audio processing](https://docs-md.agora.io/en/video-calling/advanced-features/advanced-features/stream-raw-audio.md).

Follow these steps to call the raw audio data API in your project for custom audio rendering:

1. Retrieve audio data for playback using the `onRecordAudioFrame`, `onPlaybackAudioFrame`, `onMixedAudioFrame`, or `onPlaybackAudioFrameBeforeMixing` callback.

2. Independently render and play the audio data.

## Reference

This section explains how to implement different sound effects and audio mixing in your app, covering essential steps and code snippets.

### Sample projects

Agora provides the following open-source sample projects for audio self-capture and audio self-rendering for your reference:

* [CustomAudioSource](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/customaudio/CustomAudioSource.java)
* [CustomAudioRender](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/customaudio/CustomAudioRender.java)

### API reference

* [`createCustomAudioTrack`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaengine.html#api_imediaengine_createcustomaudiotrack)

* [`destroyCustomAudioTrack`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaengine.html#api_imediaengine_destroycustomaudiotrack)

* [`pushExternalAudioFrame`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_pushaudioframe2)

* [`setExternalAudioSink`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_imediaengine_setexternalaudiosink)

* [`pullPlaybackAudioFrame` [1/2]](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_imediaengine_pullaudioframe)

* [`pullPlaybackAudioFrame` [2/2]](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_pullaudioframe2)