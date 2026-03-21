---
title: Configure audio encoding
description: ''
sidebar_position: 3
platform: android
exported_from: https://docs.agora.io/en/video-calling/enhance-call-quality/configure-audio-encoding?platform=android
exported_on: '2026-01-20T05:57:39.140898Z'
exported_file: configure-audio-encoding_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/enhance-call-quality/configure-audio-encoding?platform=android)

# Configure audio encoding

Audio quality requirements vary with application use-case. For example. in professional use-cases such as radio stations and singing competitions users are particularly sensitive to audio quality. In such cases, support for dual-channel and high-quality sound is required. High-quality sound means setting a high sampling rate and a high bitrate to achieve realistic audio. Video SDK enables you to configure audio encoding properties to meet such requirements. 

This article shows you how to use Video SDK to configure appropriate audio encoding properties and application scenarios in your app.

## Understand the tech

Video SDK uses default encoding parameters and a default audio scenario that are suitable for most common applications. If the default settings do not meet your needs, refer to the examples in the implementation section to set appropriate audio encoding properties and an application scenario.

## Prerequisites
Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project. 

## Implementation 


This section shows you how to set audio encoding properties and application scenarios for common applications. You use the following APIs to configure audio encoding:

| API | Description |
|:--- |:----------- |
| `create(config.mAudioScenario)` | While creating an `RtcEngine` instance, set the audio application scenario. The default value is `AUDIO_SCENARIO_DEFAULT`. |
| `setAudioProfile(profile)`   | You can set audio encoding properties before or after joining a channel. |
| `setAudioScenario`           | You can set an application scenario before or after joining a channel. |

Refer to the following examples to choose the most appropriate settings for your application.

### 1-on-1 interactive teaching
This use-case requires ensuring call quality and smooth transmission. Add the following code to your project:

**Java**
```java
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.DEFAULT);
engine = RtcEngine.create(config);

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_DEFAULT);
```

**Kotlin**
```kotlin
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.DEFAULT)
engine = RtcEngine.create(config)

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_DEFAULT)
```


### Gaming voice chat
This use-case requires the transmission of clear human voice with minimal background noise and at a low bitrate. Agora recommends the following settings:

**Java**
```java
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.CHATROOM);
engine = RtcEngine.create(config);

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_SPEECH_STANDARD);
```

**Kotlin**
```kotlin
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.CHATROOM)
engine = RtcEngine.create(config)

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_SPEECH_STANDARD)
```


### Scripted role play
This use-case requires good sound expression, and no volume or sound quality change when switching microphones. Agora recommends the following settings:

**Java**
```java
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.CHATROOM);
engine = RtcEngine.create(config);

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_STANDARD);
```

**Kotlin**
```kotlin
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.CHATROOM)
engine = RtcEngine.create(config)

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_STANDARD)
```


### KTV
KTV generally requires high sound quality and good expressiveness for music and singing. Use the following code in your project:

**Java**
```java
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING);
engine = RtcEngine.create(config);

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_HIGH_QUALITY);
```

**Kotlin**
```kotlin
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING)
engine = RtcEngine.create(config)

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_HIGH_QUALITY)
```


### Voice radio
Voice radio generally uses professional audio equipment. It requires high sound quality and stereo. Use the following code in your project:

**Java**
```java
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING);
engine = RtcEngine.create(config);

// Set the required audio encoding properties
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_HIGH_QUALITY_STEREO);
```  

**Kotlin**
```kotlin
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING)
engine = RtcEngine.create(config)

// Set the required audio encoding properties
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_HIGH_QUALITY_STEREO)
```


### Music teaching
This use-case requires high sound quality, and support for the transmission of speaker-played sound effects. Agora recommends the following settings:

**Java**
```java
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING);
engine = RtcEngine.create(config);

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_STANDARD_STEREO);
```  

**Kotlin**
```kotlin
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING)
engine = RtcEngine.create(config)

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_STANDARD_STEREO)
```


### Dual-teacher classroom
This use-case requires high sound quality with rich sound effects, and no volume or sound quality change when switching microphones. Agora recommends the following settings:

**Java**
```java
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.CHATROOM);
engine = RtcEngine.create(config);

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_STANDARD_STEREO);
```

**Kotlin**
```kotlin
// Create the RtcEngine instance with a specific audio application scenario
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.CHATROOM)
engine = RtcEngine.create(config)

// Define the audio encoding settings
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_STANDARD_STEREO)
```


## Reference
This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

For more audio settings, see [Achieve high audio quality](https://docs-md.agora.io/en/video-calling/best-practices/best-practices-sound-quality.md).

### Frequently asked questions​

*  [What is the difference between the in-call volume and the media volume?](https://docs-md.agora.io/en/help/integration-issues/system_volume.md)

### Sample projects

Agora offers the following open-source sample project for setting audio encoding properties and application scenario for your reference.

* [JoinChannelAudio](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/basic/JoinChannelAudio.java)

### API reference

- [`create` [2/2]](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_initialize)
- [`setAudioProfile` [2/2]](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioprofile2)
- [`setAudioScenario`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioscenario)