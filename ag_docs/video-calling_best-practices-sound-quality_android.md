---
title: Achieve high audio quality
description: Best practices for optimal audio quality.
sidebar_position: 1
platform: android
exported_from: https://docs.agora.io/en/video-calling/best-practices/best-practices-sound-quality?platform=android
exported_on: '2026-01-20T05:57:24.144869Z'
exported_file: best-practices-sound-quality_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/best-practices/best-practices-sound-quality?platform=android)

# Achieve high audio quality

Some use-cases, such as karaoke sessions, podcasts, and performance-based chats, require a high-quality audio experience. This page shows you how to achieve clear high-definition audio, without noise or interference in your app.

## General settings

To improve the audio quality experience, consider the following:


### Set audio encoding properties

Call [`setAudioProfile` [2/2]](https://api-ref.agora.io/en/voice-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioprofile2) to set the profile to `MUSIC_HIGH_QUALITY_STEREO` (5). It uses a 48 ​​kHz sampling rate, music encoding, two channels, and the maximum encoding rate is 128 Kbps.

**Java**
```java
// Set the audio profile to MUSIC_HIGH_QUALITY_STEREO
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_HIGH_QUALITY_STEREO);
```

**Kotlin**
```kotlin
// Set the audio profile to MUSIC_HIGH_QUALITY_STEREO
RtcEngine.setAudioProfile(Constants.AUDIO_PROFILE_MUSIC_HIGH_QUALITY_STEREO)
```


### Set audio use-case

Call [`setAudioScenario`](https://api-ref.agora.io/en/voice-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioscenario) to set the audio scenario to high-quality `AUDIO_SCENARIO_GAME_STREAMING` (3).

**Java**
```java
// Set the audio scenario to GAME_STREAMING
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING);
engine = RtcEngine.create(config);
```  

**Kotlin**
```kotlin
// Set the audio scenario to GAME_STREAMING
config.mAudioScenario = Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING)
engine = RtcEngine.create(config)
```


## Sound card settings

This section only applies to users using sound cards.


### Disable 3A

Video SDK turns on 3A by default. In audio processing, 3A stands for Acoustic Echo Cancellation (AEC), Active Noise Suppression (ANS), and Automatic Gain Control (AGC). Sound card devices usually provide some built-in audio processing, such as echo and noise cancellation. Currently, if 3A is enabled in the application layer, it may cause over-processing of the audio signal and interference between different algorithms may impact sound quality. Best practice is that users with sound cards disable the 3A function by calling [`setParameters`](https://api-ref.agora.io/en/voice-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setparameters).

**Java**
```java
// Turn off echo cancellation
engine.setParameters("{"che.audio.aec.enable":false}");
// Turn off noise reduction
engine.setParameters("{"che.audio.ans.enable":false}");
// Turn off gain control
engine.setParameters("{"che.audio.agc.enable":false}");
```

**Kotlin**
```kotlin
// Turn off echo cancellation
engine.setParameters("{"che.audio.aec.enable":false}")
// Turn off noise reduction
engine.setParameters("{"che.audio.ans.enable":false}")
// Turn off gain control
engine.setParameters("{"che.audio.agc.enable":false}")
```


### Turn on stereo capture

Call [`setAdvancedAudioOptions`](https://api-ref.agora.io/en/voice-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setadvancedaudiooptions) to set the number of audio pre-processing channels to `AGORA_AUDIO_STEREO_PROCESSING` (2), that is, use two channels to collect and send stereo sound.

**Java**
```java
// Set advanced audio options for 2 audio processing channels
AdvancedAudioOptions options = new AdvancedAudioOptions();
options.audioProcessingChannels = 2;
m_lpAgoraEngine.setAdvancedAudioOptions(options);
```  

**Kotlin**
```kotlin
// Set advanced audio options for 2 audio processing channels
val options = AdvancedAudioOptions()
options.audioProcessingChannels = 2
m_lpAgoraEngine.setAdvancedAudioOptions(options)
```
