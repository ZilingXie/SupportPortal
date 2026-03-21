---
title: Voice effects
description: ''
sidebar_position: 13
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/voice-effects?platform=android
exported_on: '2026-01-20T05:57:17.166801Z'
exported_file: voice-effects_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/voice-effects?platform=android)

# Voice effects

Video SDK makes it simple for you to publish audio captured through the microphone to subscribers in a channel. In some cases, users want to modify the captured audio to add voice effects, or change the voice quality before the audio is published. Video SDK provides several options that enable you to apply voice effects. This page shows you how to implement these features in your channel.

## Understand the tech

Voice effects are gaining popularity in social interaction and entertainment use-cases. To help you quickly integrate voice effects into your project, Video SDK provides pre-configured effects. Choose from the following effects:

* **Voice beautifiers**
    * Voice beautifier for Chat: Beautify the voice in chat use-cases according to the characteristics of male and female voices without changing the original voice recognition.
    * Singing beautifier: Beautify the singing voice according to male and female voice characteristics while retaining the original character of the singing voice.
    * Timbre shift: Fine-tune the timbre of a vocal in a specific direction.

* **Sound effects**
    * Style sound effects: For songs of a specific style, make the singing and accompaniment more compatible.
    * Spatial shaping: Create a spatial atmosphere through spatial reverberation effects. Make the vocals seem to come from a specific source.
    * Electronic sound effects: Adjust the pitch of the vocals to perfectly match the key and pitch of the accompaniment for an electronic sound effect.

* **Voice changer**
    * Basic voice changing: Make the voice more neutral, sweet, or stable while retain a certain degree of voice recognition.
    * Advanced voice changing: Dramatically change the human voice to realize voices of uncle, girl, Hulk, etc.

* **Custom audio effects** 
    If the preset effects don't meet your needs, manually adjust the voice pitch, equalization, and reverberation to achieve a customized effect.

Try the [Online Demo](https://web-cdn.agora.io/marketing/audio_en_v3.html) to experience different voice effects.

## Prerequisites

Before proceeding with the code examples on this page, make sure you have completed the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) guide.

## Implement voice effects

This section shows how to use different sound beautifiers to enhance your audio experience. 

Use the following methods to set the desired vocal effects:

*  Call the `setVoiceBeautifierPreset` method to apply effects such as chatting bel canto, singing bel canto, and timbre change.
*  Utilize the `setAudioEffectPreset` method to configure sound effects, genre sound effects, space shaping, and other effects.
*  Apply the `setVoiceConversionPreset` method to entirely transform the original voice.
If the preset effects don't meet your requirements, customize vocal effects using methods such as `setLocalVoicePitch`, `setLocalVoiceEqualization`, and `setLocalVoiceReverb`.

Choose the one that best fits your requirements. To implement various voice effects in your project, take the following steps:


### Set audio scenario and audio profile

For optimal audio quality, take the following steps:

- Call [setAudioScenario](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioscenario) to set the audio scenario to high-quality `AUDIO_SCENARIO_GAME_STREAMING`.
- Call [setAudioProfile](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioprofile2) to set the audio encoding properties to high-quality encoding:

   - For mono transmission, set the profile to `AUDIO_PROFILE_MUSIC_HIGH_QUALITY`.
   - For stereo transmission, set the profile to `AUDIO_PROFILE_MUSIC_HIGH_QUALITY_STEREO`

      **Java**
      ```java
      // Create the RtcEngine object
            mRtcEngine = RtcEngine.create(config);
            // Set the audio scenario
            mRtcEngine.setAudioScenario(Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING));
            // Set the audio encoding properties
            mRtcEngine.setAudioProfile(Constants.AudioProfile.getValue(Constants.AudioProfile.MUSIC_HIGH_QUALITY_STEREO));
      ```

      **Kotlin**
      ```kotlin
      // Create the RtcEngine object
            mRtcEngine = RtcEngine.create(config)
            // Set the audio scenario
            mRtcEngine.setAudioScenario(Constants.AudioScenario.getValue(Constants.AudioScenario.GAME_STREAMING))
            // Set the audio encoding properties
            mRtcEngine.setAudioProfile(Constants.AudioProfile.getValue(Constants.AudioProfile.MUSIC_HIGH_QUALITY_STEREO))
      ```


### Voice beautifiers

Call `setVoiceBeautifierPreset` to set music style, space shaping, electronic music, and other effects.

- Chat voice beautifier

   **Java**
   ```java
   // Set the vocal effect to magnetic (for male voices)
     mRtcEngine.setVoiceBeautifierPreset(Constants.CHAT_BEAUTIFIER_MAGNETIC);
     // Turn off the effect        
     mRtcEngine.setVoiceBeautifierPreset(Constants.VOICE_BEAUTIFIER_OFF);
   ```

   **Kotlin**
   ```kotlin
   // Set the vocal effect to magnetic (for male voices)
     mRtcEngine.setVoiceBeautifierPreset(Constants.CHAT_BEAUTIFIER_MAGNETIC)
     // Turn off the effect        
     mRtcEngine.setVoiceBeautifierPreset(Constants.VOICE_BEAUTIFIER_OFF)
   ```


- Singing voice beautifier

   **Java**
   ```java
   // Example 1: Set male voice effect
     // Set the singing voice preset to beautify the male voice and add a small room reverberation effect
     mRtcEngine.setVoiceBeautifierPreset(Constants.SINGING_BEAUTIFIER);
     // Turn off the effect 
     mRtcEngine.setVoiceBeautifierPreset(Constants.VOICE_BEAUTIFIER_OFF);
   
     // Example 2: Set female voice effect
     // Call the setVoiceBeautifierParameters method to beautify the female voice and add hall reverberation effect
     mRtcEngine.setVoiceBeautifierParameters(Constants.SINGING_BEAUTIFIER, 2, 3);
     // Turn off the effect
     mRtcEngine.setVoiceBeautifierPreset(Constants.VOICE_BEAUTIFIER_OFF);
   ```

   **Kotlin**
   ```kotlin
   // Example 1: Set male voice effect
     // Set the singing voice preset to beautify the male voice and add a small room reverberation effect
     mRtcEngine.setVoiceBeautifierPreset(Constants.SINGING_BEAUTIFIER)
     // Turn off the effect 
     mRtcEngine.setVoiceBeautifierPreset(Constants.VOICE_BEAUTIFIER_OFF)
   
     // Example 2: Set female voice effect
     // Call the setVoiceBeautifierParameters method to beautify the female voice and add hall reverberation effect
     mRtcEngine.setVoiceBeautifierParameters(Constants.SINGING_BEAUTIFIER, 2, 3)
     // Turn off the effect
     mRtcEngine.setVoiceBeautifierPreset(Constants.VOICE_BEAUTIFIER_OFF)
   ```


* Tone change

   **Java**
   ```java
   // Set the timbre to thick
     mRtcEngine.setVoiceBeautifierPreset(Constants.TIMBRE_TRANSFORMATION_VIGOROUS);
     // Turn off the effect
     mRtcEngine.setVoiceBeautifierPreset(Constants.VOICE_BEAUTIFIER_OFF);
   ```

   **Kotlin**
   ```kotlin
   // Set the timbre to thick
     mRtcEngine.setVoiceBeautifierPreset(Constants.TIMBRE_TRANSFORMATION_VIGOROUS)
     // Turn off the effect
     mRtcEngine.setVoiceBeautifierPreset(Constants.VOICE_BEAUTIFIER_OFF)
   ```


### Sound effects

Call `setAudioEffectPreset` to set music style, space shaping, electronic music and other effects.

* Music Style

   **Java**
   ```java
   // Set the style to pop
     mRtcEngine.setAudioEffectPreset(Constants.STYLE_TRANSFORMATION_POPULAR);
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF);
   ```

   **Kotlin**
   ```kotlin
   // Set the style to pop
     mRtcEngine.setAudioEffectPreset(Constants.STYLE_TRANSFORMATION_POPULAR)
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF)
   ```


* Space shaping

   **Java**
   ```java
   // Set the space shaping effect to KTV
     mRtcEngine.setAudioEffectPreset(Constants.ROOM_ACOUSTICS_KTV);
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF);
   ```

   **Kotlin**
   ```kotlin
   // Set the space shaping effect to KTV
     mRtcEngine.setAudioEffectPreset(Constants.ROOM_ACOUSTICS_KTV)
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF)
   ```


* Electronic sound effects

   **Java**
   ```java
   // Example 1: Use the preset pitch adjustment method to achieve the electronic music sound effect
     // The preset is based on the natural major key with the tonic pitch of C, and corrects the actual pitch of the audio
     mRtcEngine.setAudioEffectPreset(Constants.PITCH_CORRECTION);
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF);
   
     // Example 2: Adjust the basic mode and tonic pitch to achieve the electronic music sound effect
     // Call setAudioEffectParameters to adjust the basic mode of the tone to the natural minor key and the tonic pitch to D
     mRtcEngine.setAudioEffectParameters(Constants.PITCH_CORRECTION, 2, 6);
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF);
   ```

   **Kotlin**
   ```kotlin
   // Example 1: Use the preset pitch adjustment method to achieve the electronic music sound effect
     // The preset is based on the natural major key with the tonic pitch of C, and corrects the actual pitch of the audio
     mRtcEngine.setAudioEffectPreset(Constants.PITCH_CORRECTION)
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF)
   
     // Example 2: Adjust the basic mode and tonic pitch to achieve the electronic music sound effect
     // Call setAudioEffectParameters to adjust the basic mode of the tone to the natural minor key and the tonic pitch to D
     mRtcEngine.setAudioEffectParameters(Constants.PITCH_CORRECTION, 2, 6)
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF)
   ```


### Voice change effects

To implement basic voice-changing effects, call the `setAudioEffectPreset` method. For advanced voice-changing effects, use the `setVoiceConversionPreset` method.

* Basic voice changer

   **Java**
   ```java
   // Set the voice change effect to a more neutral voice
     mRtcEngine.setVoiceConversionPreset(Constants.VOICE_CHANGER_NEUTRAL);
     // Turn off the effect
     mRtcEngine.setVoiceConversionPreset(Constants.VOICE_CONVERSION_OFF);
   ```

   **Kotlin**
   ```kotlin
   // Set the voice change effect to a more neutral voice
     mRtcEngine.setVoiceConversionPreset(Constants.VOICE_CHANGER_NEUTRAL)
     // Turn off the effect
     mRtcEngine.setVoiceConversionPreset(Constants.VOICE_CONVERSION_OFF)
   ```


* Advanced voice changer

   **Java**
   ```java
   // Set the vocal effect to 'Hulk'
     mRtcEngine.setAudioEffectPreset(Constants.VOICE_CHANGER_EFFECT_HULK);
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF);
   ```

   **Kotlin**
   ```kotlin
   // Set the vocal effect to 'Hulk'
     mRtcEngine.setAudioEffectPreset(Constants.VOICE_CHANGER_EFFECT_HULK)
     // Turn off the effect
     mRtcEngine.setAudioEffectPreset(Constants.AUDIO_EFFECT_OFF)
   ```


### Custom audio effects

Call `setLocalVoicePitch`, `setLocalVoiceEqualization`, and `setLocalVoiceReverb` to fine-tune vocal output parameters, including pitch, equalization, and reverberation effects. The following example shows you how to transform a human voice into the voice of the Hulk by manually setting parameter values:

**Java**
```java
double pitch = 0.5;
mRtcEngine.setLocalVoicePitch(pitch);

// Set the center frequency of the local voice equalization band
// The first parameter is the spectrum sub-band index, the value range is [0,9], representing 10 frequency bands, and the corresponding center frequencies are [31,62,125,250,500,1000,2000,4000,8000,16000] Hz
// The second parameter is the gain value of each frequency interval, the value range is [-15,15], the unit is dB, the default value is 0
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(0), -15);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(1), 3);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(2), -9);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(3), -8);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(4), -6);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(5), -4);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(6), -3);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(7), -2);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(8), -1);
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(9), 1);

// Original voice intensity or dry signal, value range [-20,10], unit is dB
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(0), 10);

// Early reflection signal intensity or wet signal, value range [-20,10], unit is dB
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(1), 7);

// The room size for the required reverberation effect. Generally, the larger the room, the stronger the reverberation effect. Value range: [0,100]
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(2), 6);

// Initial delay length of wet signal, value range: [0,200], unit: ms
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(3), 124);

// The continuous strength of reverberation effect, value range: [0,100], the larger the value, the stronger the reverberation effect
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(4), 78);
```

**Kotlin**
```kotlin
// Set the pitch for the local voice
val pitch = 0.5
mRtcEngine.setLocalVoicePitch(pitch)

// Set the center frequency of the local voice equalization band
// The first parameter is the spectrum sub-band index, the value range is [0,9], representing 10 frequency bands, and the corresponding center frequencies are [31,62,125,250,500,1000,2000,4000,8000,16000] Hz
// The second parameter is the gain value of each frequency interval, the value range is [-15,15], the unit is dB, the default value is 0
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(0), -15)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(1), 3)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(2), -9)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(3), -8)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(4), -6)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(5), -4)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(6), -3)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(7), -2)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(8), -1)
mRtcEngine.setLocalVoiceEqualization(Constants.AUDIO_EQUALIZATION_BAND_FREQUENCY.fromInt(9), 1)

// Original voice intensity or dry signal, value range [-20,10], unit is dB
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(0), 10)

// Early reflection signal intensity or wet signal, value range [-20,10], unit is dB
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(1), 7)

// The room size for the required reverberation effect. Generally, the larger the room, the stronger the reverberation effect. Value range: [0,100]
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(2), 6)

// Initial delay length of wet signal, value range: [0,200], unit: ms
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(3), 124)

// The continuous strength of reverberation effect, value range: [0,100], the larger the value, the stronger the reverberation effect
mRtcEngine.setLocalVoiceReverb(Constants.AUDIO_REVERB_TYPE.fromInt(4), 78)
```


## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Development considerations

- Only one vocal effect can be set at a time. If multiple effects are set, the last one overwrites the previous one.

- The enumerations in `setVoiceBeautifierPreset`, `setAudioEffectPreset`, `setVoiceConversionPreset`, and other preset methods are optimized for different genders and should be used appropriately. Using these presets on vocals of the opposite gender may cause distortion. For more details, see [API reference](#api-reference).

### Sample project

Agora provides an open source [Voice effects project](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample-Audio/app/src/main/java/io/agora/api/example/examples/advanced/VoiceEffects.java) on GitHub for your reference. Download or view the source code for a more detailed example.

### API reference

#### Audio scenario and audio profile

* [`setAudioProfile`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioprofile2)

* [`setAudioScenario`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioscenario)

#### Preset voice effects

* [`setVoiceBeautifierPreset`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setvoicebeautifierpreset)

* [`setAudioEffectPreset`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioeffectpreset)

* [`setVoiceConversionPreset`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setvoiceconversionpreset)

* [`setAudioEffectParameters`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setaudioeffectparameters)

* [`setVoiceBeautifierParameters`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setvoicebeautifierparameters)

#### Custom voice effects

* [`setLocalVoiceEqualization`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setlocalvoiceequalization)

* [`setLocalVoiceReverb`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setlocalvoicereverb)

* [`setLocalVoicePitch`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setlocalvoicepitch)