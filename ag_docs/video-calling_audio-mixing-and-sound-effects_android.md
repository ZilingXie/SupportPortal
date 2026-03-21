---
title: Audio mixing and sound effects
description: Play audio files and add sound effects to enhance the audio experience.
sidebar_position: 7
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/audio-mixing-and-sound-effects?platform=android
exported_on: '2026-01-20T05:56:15.389514Z'
exported_file: audio-mixing-and-sound-effects_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/audio-mixing-and-sound-effects?platform=android)

# Audio mixing and sound effects

Video SDK makes it simple for you to publish audio captured through the microphone to subscribers in a channel. In some real-time audio and video use-cases, such as games or karaoke, you need to play sound effects or mix in music files to enhance the atmosphere and add interest. Video SDK enables you to add sound effects and mix in pre-recorded audio.

This page shows you how to implement audio mixing and playing sound effects in your app.

## Understand the tech

Video SDK provides APIs that enable you to implement:

    * **Audio mixing**
    
        Mix in music file such as background music with microphone audio. Using this feature, you can play only one file at a time.
 
    * **Sound effects**
        
        Play audios with a short duration. For example, applause, cheers, or gunshots. You can play multiple sound effects at the same time.

## Prerequisites

Ensure that you have: 

* Implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

* Audio files in one of the [supported formats](https://developer.android.com/media/platform/supported-formats).

* Added the required permission to your project

    If your project's `targetSdkVersion` is greater than 20, add the following code to the `AndroidManifest.xml` file:
        ```xml
        <application>
            <!-- Other application settings -->
            android:usesCleartextTraffic="true"
            android:requestLegacyExternalStorage="true"
        </application>
        ```

## Play sound effects and music

This section shows you how to implement playing sound effects and add audio mixing in your app.


To manage audio mixing and sound effects, Video SDK provides the following APIs:

| Function | Sound effect | Audio mixing |
|----------|--------------|-----------------------------|
| Play or stop playing a specific audio file     | `preloadEffect`<br/> `unloadEffect`<br/> `playEffect`<br/> `stopEffect`<br/> `stopAllEffects`  | `startAudioMixing`<br/> `stopAudioMixing`       |
| Pause or resume playing an audio file          | `pauseEffect`<br/> `pauseAllEffects`<br/> `resumeEffect`<br/> `resumeAllEffects` | `pauseAudioMixing`<br/> `resumeAudioMixing`    |
| Get and adjust playback position and volume   | `setEffectPosition`<br/> `getEffectCurrentPosition`<br/> `getEffectsVolume`<br/> `setEffectsVolume`<br/> `setVolumeOfEffect` | `getAudioMixingCurrentPosition`<br/> `setAudioMixingPosition`<br/> `getAudioMixingPublishVolume`<br/> `adjustAudioMixingPublishVolume`<br/> `getAudioMixingPlayoutVolume`<br/> `adjustAudioMixingPlayoutVolume` |
| Report playback status of audio files          | `onAudioEffectFinished`                       | `onAudioMixingStateChanged`                    |

### Set up file access permissions 
For Android projects with `targetSdkVersion` greater than or equal to 20, add the following to the project's `AndroidManifest.xml` file:

    ```xml
    <application>
        android:usesCleartextTraffic="true"
        android:requestLegacyExternalStorage="true"
    </application>
    ```

### Play sound effects

To play sound effects, refer to the following code example:

**Java**
```java
//Import the IAudioEffectManger class
import io.agora.rtc.IAudioEffectManager;

// Call getAudioEffectManager to get the IAudioEffectManager class
private IAudioEffectManager audioEffectManager;
audioEffectManager = mRtcEngine.getAudioEffectManager();

// Set the sound effect ID as a unique identifier for identifying sound effect files
int id = 0;
// If you want to play the sound effect repeatedly, you can preload the file into memory
// If the file is large, do not preload it
// You can only preload local sound effect files
audioEffectManager.preloadEffect(id++, "Your file path");

// Call playEffect to play the specified sound effect file
// Call playEffect multiple times, set multiple sound effect IDs, and play multiple sound effect files at the same time
audioEffectManager.playEffect(
    0,    // Set the sound effect ID
    "Your file path",   // Set the sound effect file path
    -1,   // Set the number of times the sound effect loops. -1 means infinite loop
    1,    // Set the tone of the sound effect. 1 represents the original pitch
    0.0,  // Set the spatial position of the sound effect. 0.0 means the sound effect appears directly in front
    100,  // Set the sound effect volume. 100 represents the original volume
    true, // Set whether to publish sound effects to the remote end
    0     // Set the playback position of the sound effect file (in ms). 0 means start at the beginning
);

// Pause or resume playing the specified sound effect file
audioEffectManager.pauseEffect(id);
audioEffectManager.resumeEffect(id);

// Set the playback position of the specified local sound effect file
audioEffectManager.setEffectPosition(id, 500);

// Set the playback volume of all sound effect files
audioEffectManager.setEffectsVolume(50.0);

// Set the playback volume of the specified sound effect file
audioEffectManager.setVolumeOfEffect(id, 50.0);

// Release the preloaded sound effect file
audioEffectManager.unloadEffect(id);

// Stop playing all sound effect files
audioEffectManager.stopAllEffects;

@Override
// This callback is triggered when the sound effect file ends playing
public void onAudioEffectFinished(int soundId) {
    super.onAudioEffectFinished(soundId);
}
```

**Kotlin**
```kotlin
// Import the IAudioEffectManager class
import io.agora.rtc.IAudioEffectManager

// Call getAudioEffectManager to get the IAudioEffectManager class
private lateinit var audioEffectManager: IAudioEffectManager

audioEffectManager = mRtcEngine.getAudioEffectManager()

// Set the sound effect ID as a unique identifier for identifying sound effect files
var id = 0
// If you want to play the sound effect repeatedly, you can preload the file into memory
// If the file is large, do not preload it
// You can only preload local sound effect files
audioEffectManager.preloadEffect(id++, "Your file path")

// Call playEffect to play the specified sound effect file
// Call playEffect multiple times, set multiple sound effect IDs, and play multiple sound effect files at the same time
audioEffectManager.playEffect(
    0,    // Set the sound effect ID
    "Your file path",   // Set the sound effect file path
    -1,   // Set the number of times the sound effect loops. -1 means infinite loop
    1,    // Set the tone of the sound effect. 1 represents the original pitch
    0.0,  // Set the spatial position of the sound effect. 0.0 means the sound effect appears directly in front
    100,  // Set the sound effect volume. 100 represents the original volume
    true, // Set whether to publish sound effects to the remote end
    0     // Set the playback position of the sound effect file (in ms). 0 means start at the beginning
)

// Pause or resume playing the specified sound effect file
audioEffectManager.pauseEffect(id)
audioEffectManager.resumeEffect(id)

// Set the playback position of the specified local sound effect file
audioEffectManager.setEffectPosition(id, 500)

// Set the playback volume of all sound effect files
audioEffectManager.setEffectsVolume(50.0)

// Set the playback volume of the specified sound effect file
audioEffectManager.setVolumeOfEffect(id, 50.0)

// Release the preloaded sound effect file
audioEffectManager.unloadEffect(id)

// Stop playing all sound effect files
audioEffectManager.stopAllEffects()

// This callback is triggered when the sound effect file ends playing
override fun onAudioEffectFinished(soundId: Int) {
   super.onAudioEffectFinished(soundId)
}
```


### Incorporate audio mixing

Before or after joining a channel, call `startAudioMixing` to play the audio file. When the audio mixing status changes, the SDK triggers the `onAudioMixingStateChanged` callback and reports the reason for the change. 

To mix in an audio file, refer to the following code example:

**Java**
```java
// Start playing a music file
mRtcEngine.startAudioMixing(
    "Your file path",  // Specify the path of the local or online music file
    false,             // Set whether to play the music file only locally. false means both local and remote users can hear the music
    -1,             // Set the number of times the music file should be played. -1 indicates infinite loop
    0                 // Set the starting playback position of a music file
);

@Override
// Triggered when the playback state of the music file changes
// After receiving the onAudioMixingStateChanged callback, call other mixing APIs, such as pauseAudioMixing or getAudioMixingDuration
public void onAudioMixingStateChanged(int state, int errorCode) {
    super.onAudioMixingStateChanged(state, errorCode);
}

// Pause or resume playing the music file
rtcEngine.pauseAudioMixing();
rtcEngine.resumeAudioMixing();

// Get the total duration of the current music file
rtcEngine.getAudioMixingDuration();

// Set the playback position of the current music file. 500 indicates starting playback from the 500th ms of the music file
rtcEngine.setAudioMixingPosition(500);

// Adjust the playback volume of the current music file for remote users
rtcEngine.adjustAudioMixingPublishVolume(50);

// Adjust the playback volume of the current music file locally
rtcEngine.adjustAudioMixingPlayoutVolume(50);
```

**Kotlin**
```kotlin
// Start playing a music file
mRtcEngine.startAudioMixing(
    "Your file path",  // Specify the path of the local or online music file
    false,             // Set whether to play the music file only locally. false means both local and remote users can hear the music
    -1,                // Set the number of times the music file should be played. -1 indicates infinite loop
    0                  // Set the starting playback position of a music file
)

// Override callback for audio mixing state changes
override fun onAudioMixingStateChanged(state: Int, errorCode: Int) {
    super.onAudioMixingStateChanged(state, errorCode)
}

// Pause or resume playing the music file
rtcEngine.pauseAudioMixing()
rtcEngine.resumeAudioMixing()

// Get the total duration of the current music file
rtcEngine.getAudioMixingDuration()

// Set the playback position of the current music file (e.g., 500 ms)
rtcEngine.setAudioMixingPosition(500)

// Adjust the playback volume of the current music file for remote users
rtcEngine.adjustAudioMixingPublishVolume(50)

// Adjust the playback volume of the current music file locally
rtcEngine.adjustAudioMixingPlayoutVolume(50)
```


Control playback using the following methods:

- `pauseAudioMixing`: Pause playback.
- `resumeAudioMixing`: Resume playback.
- `stopAudioMixing`: Stop playing.
- `setAudioMixingPosition`: Set the playing position of the current audio file.
- `adjustAudioMixingPlayoutVolume`: Adjust the volume of the current audio file played locally.
- `adjustAudioMixingPublishVolume`: Adjust the volume of the current audio file played at the remote end.

> ⚠️ **Caution**
> If you play a short sound effect file using `startAudioMixing`, or a long music file using `playEffect`, the playback may fail.

## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### FAQs

* [Why can't I play the audio file using `startAudioMixing` or `playEffect` on Android 9?](https://docs-md.agora.io/en/help/integration-issues/android_startaudiomixing_permission.md)

### Sample project

Agora provides an open source [Play Audio Files](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample-Audio/app/src/main/java/io/agora/api/example/examples/advanced/PlayAudioFiles.java) project on GitHub for your reference. Download or view the source code for a more detailed example.

### API reference

* [`preloadEffect`](https://api-ref.agora.io/en/video-sdk/android/4.x/APIclass_iaudioeffectmanager.html#api_irtcengine_preloadeffect)

* [`playEffect`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_iaudioeffectmanager.html#api_irtcengine_playeffect3)

* [`onAudioEffectFinished`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onaudioeffectfinished)

* [`startAudioMixing`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_startaudiomixing)

* [`onAudioMixingStateChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onaudiomixingstatechanged)

* [`pauseAudioMixing`](https://api-ref.agora.io/en/video-sdk/android/4.x/APIclass_irtcengine.html#api_irtcengine_pauseaudiomixing)

* [`resumeAudioMixing`](https://api-ref.agora.io/en/video-sdk/android/4.x/APIclass_irtcengine.html#api_irtcengine_resumeaudiomixing)

* [`stopAudioMixing`](https://api-ref.agora.io/en/video-sdk/android/4.x/APIclass_irtcengine.html#api_irtcengine_stopaudiomixing)

* [`adjustAudioMixingPlayoutVolume`](https://api-ref.agora.io/en/video-sdk/android/4.x/APIclass_irtcengine.html#api_irtcengine_adjustaudiomixingplayoutvolume)

* [`adjustAudioMixingPublishVolume`](https://api-ref.agora.io/en/video-sdk/android/4.x/APIclass_irtcengine.html#api_irtcengine_adjustaudiomixingpublishvolume)