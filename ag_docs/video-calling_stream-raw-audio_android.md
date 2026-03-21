---
title: Raw audio processing
description: Pre and post-process captured video and audio data to achieve the desired
  playback effect.
sidebar_position: 5
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/stream-raw-audio?platform=android
exported_on: '2026-01-20T05:57:05.634462Z'
exported_file: stream-raw-audio_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/stream-raw-audio?platform=android)

# Raw audio processing

In some use-cases, raw audio captured through the microphone must be processed to enhance the user experience or achieve the desired functionality Video SDK enables you to pre-process and post-process the captured audio for implementation of custom playback effects.

This article shows you how to pre-process and post-process collected raw audio data.

## Understand the tech

For use-cases that require self-processing of audio data, Agora Video SDK provides raw data processing functionality. You can perform pre-processing to modify the captured audio signal before sending the data to the encoder, or post-process data to modify the received audio signal after sending the data to the decoder.

To implement processing of raw audio data in your app, take the following steps.

- Register an instance of the audio frame observer before joining a channel.
- Set the format of audio frames captured by each callback.
- Implement callbacks in the frame observers to process raw audio data.
- Unregister the frame observers before you leave a channel.

The following figure shows the basic processing of raw audio data:

**Process raw audio**

![Raw Audio Processing](https://docs-md.agora.io/images/video-sdk/process-raw-audio.svg)

## Prerequisites
Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implement raw audio processing


Follow these steps to implement raw audio data processing functionality in your app:

1. Before joining a channel, create an `IAudioFrameObserver` instance and call the `registerAudioFrameObserver` method to register the audio observer.
1. Call `setRecordingAudioFrameParameters`, `setPlaybackAudioFrameParameters`, and `setMixedAudioFrameParameters` to configure the audio frame format.
1. Implement `onRecordAudioFrame`, `onPlaybackAudioFrame`, `onPlaybackAudioFrameBeforeMixing`, and `onMixedAudioFrame` callbacks. These callbacks receive and process audio frames. If the return value of these callbacks is `false`, it indicates that the processing of the audio frames is invalid.

Refer to the following sample code to implement this logic:

**Java**
```java
// Call registerAudioFrameObserver to register an audio observer and pass in an IAudioFrameObserver instance
engine.registerAudioFrameObserver(new IAudioFrameObserver() {
    // Implement the onRecordAudioFrame callback
    @Override
    public boolean onRecordAudioFrame(byte[] samples, int numOfSamples, int bytesPerSample, int channels, int samplesPerSec) {
        if(isEnableLoopBack){
            mAudioPlayer.play(samples, 0, numOfSamples * bytesPerSample);
        }

        return false;
    }
        
    // Implement the onPlaybackAudioFrame callback
    @Override
    public boolean onPlaybackAudioFrame(byte[] samples, int numOfSamples, int bytesPerSample, int channels, int samplesPerSec) {
        return false;
    }
        
    // Implement the onPlaybackAudioFrameBeforeMixing callback
    @Override
    public boolean onPlaybackAudioFrameBeforeMixing(byte[] samples, int numOfSamples, int bytesPerSample, int channels, int samplesPerSec, int uid) {
        return false;
    }

    // Implement the onMixedAudioFrame callback
    @Override
    public boolean onMixedAudioFrame(byte[] samples, int numOfSamples, int bytesPerSample, int channels, int samplesPerSec) {
        return false;
    }

    // Call methods with 'set' prefix to configure the audio frames captured by each callback
    engine.setRecordingAudioFrameParameters(SAMPLE_RATE, SAMPLE_NUM_OF_CHANNEL, Constants.RAW_AUDIO_FRAME_OP_MODE_READ_WRITE, SAMPLES_PER_CALL);
    engine.setMixedAudioFrameParameters(SAMPLE_RATE, SAMPLES_PER_CALL);
    engine.setPlaybackAudioFrameParameters(SAMPLE_RATE, SAMPLE_NUM_OF_CHANNEL, Constants.RAW_AUDIO_FRAME_OP_MODE_READ_WRITE, SAMPLES_PER_CALL);
});
```

**Kotlin**
```kotlin
// Register an audio frame observer using registerAudioFrameObserver with an IAudioFrameObserver instance
engine.registerAudioFrameObserver(object : IAudioFrameObserver {

    // Implement the onRecordAudioFrame callback
    override fun onRecordAudioFrame(
        samples: ByteArray, 
        numOfSamples: Int, 
        bytesPerSample: Int, 
        channels: Int, 
        samplesPerSec: Int
    ): Boolean {
        if (isEnableLoopBack) {
            mAudioPlayer.play(samples, 0, numOfSamples * bytesPerSample)
        }
        return false
    }

    // Implement the onPlaybackAudioFrame callback
    override fun onPlaybackAudioFrame(
        samples: ByteArray, 
        numOfSamples: Int, 
        bytesPerSample: Int, 
        channels: Int, 
        samplesPerSec: Int
    ): Boolean {
        return false
    }

    // Implement the onPlaybackAudioFrameBeforeMixing callback
    override fun onPlaybackAudioFrameBeforeMixing(
        samples: ByteArray, 
        numOfSamples: Int, 
        bytesPerSample: Int, 
        channels: Int, 
        samplesPerSec: Int, 
        uid: Int
    ): Boolean {
        return false
    }

    // Implement the onMixedAudioFrame callback
    override fun onMixedAudioFrame(
        samples: ByteArray, 
        numOfSamples: Int, 
        bytesPerSample: Int, 
        channels: Int, 
        samplesPerSec: Int
    ): Boolean {
        return false
    }
})

// Call methods with 'set' prefix to configure the audio frames captured by each callback
engine.setRecordingAudioFrameParameters(SAMPLE_RATE, SAMPLE_NUM_OF_CHANNEL, Constants.RAW_AUDIO_FRAME_OP_MODE_READ_WRITE, SAMPLES_PER_CALL)
engine.setMixedAudioFrameParameters(SAMPLE_RATE, SAMPLES_PER_CALL)
engine.setPlaybackAudioFrameParameters(SAMPLE_RATE, SAMPLE_NUM_OF_CHANNEL, Constants.RAW_AUDIO_FRAME_OP_MODE_READ_WRITE, SAMPLES_PER_CALL)
```

> ⚠️ **Precaution**
> Video SDK uses a synchronous callback mechanism for processing raw audio data. When you save or rewrite data using the callbacks, consider the following best practices:
>     - To ensure continuity of the audio stream, do not block the SDK thread by processing data directly in the callback function. Instead, make a deep copy of the received audio data and transfer the copied data to another thread for processing.
>     - If you choose to process the audio data synchronously within the callback function, you must strictly control the processing time. For example, if the callback function is triggered every 10 milliseconds, then the processing time within the callback must be less than 10 milliseconds to prevent delays or interruptions in the audio stream.

## Reference
This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

* [Audio module](https://docs-md.agora.io/en/video-calling/overview/core-concepts_android.md)

### Sample project

Agora provides an open-source example project [ProcessAudioRawData](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/ProcessAudioRawData.java) for your reference. Download or view the project for a more detailed example.

### API reference

- [`registerAudioFrameObserver`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_imediaengine_registeraudioframeobserver)
- [`setRecordingAudioFrameParameters`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setrecordingaudioframeparameters)
- [`setPlaybackAudioFrameParameters`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setplaybackaudioframeparameters)
- [`setMixedAudioFrameParameters`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setmixedaudioframeparameters)
- [`onRecordAudioFrame`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_iaudioframeobserver_2.html#callback_iaudioframeobserverbase_onrecordaudioframe)
- [`onPlaybackAudioFrame`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_iaudioframeobserver_2.html#callback_iaudioframeobserverbase_onplaybackaudioframe)
- [`onplaybackaudioframebeforemixing`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_iaudioframeobserver_2.html#callback_iaudioframeobserver_onplaybackaudioframebeforemixing)
- [`onMixedAudioFrame`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_iaudioframeobserver_2.html#callback_iaudioframeobserverbase_onmixedaudioframe)