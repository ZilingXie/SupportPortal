---
title: 3D Spatial Audio
description: Play sounds that have an audible location.
sidebar_position: 17
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/spatial-audio?platform=android
exported_on: '2026-01-20T05:57:02.904190Z'
exported_file: spatial-audio_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/spatial-audio?platform=android)

# 3D Spatial Audio

3D Spatial Audio brings the real-world sound experience to the virtual world, providing an immersive audio experience for users. Agora's spatial audio technology enables you to simulate the propagation characteristics of sound in a physical environment within a virtual interactive scene.

![Spatial audio comparison](https://docs-md.agora.io/images/video-sdk/spatial-audio-comparison.png)
 
- Ultra-realistic space shaping effect

    Utilize technologies such as range audio, sound blur, and air attenuation simulation to perfectly simulate the real auditory experience.

    - Set the spatial positions of users in real time to give a sense of change in the distance, direction, and orientation of other users.
    - Update the spatial position of the media player, to add a sense of space to background sounds, accompaniments, and other media resources.
    - Add 3D Spatial Audio effects such as sound blurring and air attenuation by adjusting audio settings to perfectly simulate the real audio experience.

- 3D High Fidelity

    - Sound effects are processed and rendered based on the facial orientation, sound source orientation, and relative position of the sound source in 3D space.
    - Supports 48 kHz full-band sampling and 3D high-fidelity audio processing and rendering.

- Multi-platform support

    Supports iOS, Android, macOS, Windows, Web, Unity, Flutter, React Native, Electron, Unreal and other platforms.

- Ultra-low latency, low power consumption, and low cost

    The spatial audio algorithm adopts an advanced front-end processing mode and synchronizes spatial coordinates through cloud services. The end-cloud collaborative processing mode effectively reduces overall latency and power consumption.

Compared to traditional stereo, which relies on left and right channels, spatial audio technology greatly enhances the depth and realism of sound. The following table highlights these enhancements:

| Feature    | Traditional Stereo | Agora Spatial Audio   |
|:-----------|:------------------|:-----------------------|
| Dimensionality  | Left and right dimensions      | Represents sound in a full 3D space using the x, y, and z axes of the world coordinate system, corresponding to right, top, and front dimensions   |
| Spatial Perception     | Adjust the volume of the left and right channels to create a spatial sound| Utilizes advanced spatial audio algorithms to create a realistic soundscape by manipulating parameters like distance, direction, and orientation |
| User Experience    | Flat     | Immersive, three-dimensional, and natural, delivering a realistic auditory experience     |

This page shows you how to implement 3D Spatial Audio in your app.

## Understand the tech

Agora provides a local Cartesian coordinate system calculation scheme for setting up 3D Spatial Audio positions for users and the media player.

#### 3D Spatial Audio for users

Use an instance of `ILocalSpatialAudioEngine` to implement 3D Spatial Audio. Call `updateSelfPosition` and `updateRemotePosition` to specify the spatial coordinates of local and remote users in the channel. Video SDK calculates the relative positions of local and remote users. This enables local users to experience the 3D Spatial Audio of remote users.

![SpatialAudio](https://docs-md.agora.io/images/video-sdk/spatial-audio-effect-user.svg)

#### 3D Spatial Audio for media player

Call `updateSelfPosition` and `updatePlayerPositionInfo` methods of the `ILocalSpatialAudioEngine` class to update the spatial coordinates of local users and the media player. The SDK calculates the relative positions of local users and the media player. This enables local users to experience the 3D Spatial Audio effect of the media player.

![SpatialAudio](https://docs-md.agora.io/images/video-sdk/spatial-audio-effect-player.svg)

## Prerequisites

Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project. 

## Implement 3D Spatial Audio
The following figure shows the workflow you implement to provide 3D Spatial Audio for users and the media player:

**Spatial audio steps**

![Spatial audio steps](https://docs-md.agora.io/images/video-sdk/spatial-audio-steps.svg)


### Initialize the spatial audio engine

Call the `create` method of the `ILocalSpatialAudioEngine` class to create an instance of the spatial audio engine. Then, call `initialize` to enable spatial audio.

**Java**
```java
localSpatial = ILocalSpatialAudioEngine.create();
LocalSpatialAudioConfig localSpatialAudioConfig = new LocalSpatialAudioConfig();
localSpatialAudioConfig.mRtcEngine = engine;
localSpatial.initialize(localSpatialAudioConfig);
```

**Kotlin**
```kotlin
localSpatial = ILocalSpatialAudioEngine.create()
val localSpatialAudioConfig = LocalSpatialAudioConfig()
localSpatialAudioConfig.mRtcEngine = engine
localSpatial.initialize(localSpatialAudioConfig)
```


### Set the audio profile and scenario

To set the desired audio encoding properties, call `setAudioProfile`.

Call `setAudioScenario` to set the scenario to `AUDIO_SCENARIO_GAME_STREAMING` for the best sound quality effect.

**Java**
```java
engine.setAudioProfile(io.agora.rtc2.Constants.AUDIO_PROFILE_MUSIC_STANDARD);
engine.setAudioScenario(io.agora.rtc2.Constants.AUDIO_SCENARIO_GAME_STREAMING);
```

**Kotlin**
```kotlin
engine.setAudioProfile(io.agora.rtc2.Constants.AUDIO_PROFILE_MUSIC_STANDARD)
engine.setAudioScenario(io.agora.rtc2.Constants.AUDIO_SCENARIO_GAME_STREAMING)
```


### Set the audio reception range

To set the maximum number of audio streams that can be received within the audio receiving range, call `setMaxAudioRecvCount`. The recommended `maxCount` value is ≤ 16.

To set the maximum range of receivable audio, in meters, call `setAudioRecvRange`. The recommended value range is ＞ 0.

**Java**
```java
localSpatial.setMaxAudioRecvCount(2);
localSpatial.setAudioRecvRange(AXIS_MAX_DISTANCE);
```

**Kotlin**
```kotlin
localSpatial.setMaxAudioRecvCount(2)
localSpatial.setAudioRecvRange(AXIS_MAX_DISTANCE)
```


### Update spatial position
In the user space audio scenario, call the `updateSelfPosition` and `updateRemotePosition` methods to update the positions of the local user and remote users respectively.

In the media player spatial audio scenario, call the `updateSelfPosition` and `updatePlayerPositionInfo` methods to update the position of the local user and the media player, respectively.

Typically, you call these methods when:

- A new user joins the channel.
- The relative positions of the local user, a remote user, or the media player changes.
- There are other changes in your specific scenario.

**Java**
```java
// Update local user position
float[] pos = getVoicePosition(localIv);
float[] forward = new float[]{1.0F, 0.0F, 0.0F};
float[] right = new float[]{0.0F, 1.0F, 0.0F};
float[] up = new float[]{0.0F, 0.0F, 1.0F};
localSpatial.updateSelfPosition(pos, forward, right, up);

// Update the remote user's position
if (remoteLeftTv.getTag() == null) {
    remoteLeftTv.setTag(uid);
    remoteLeftTv.setVisibility(View.VISIBLE);
    remoteLeftTv.setText(uid + "");
    RemoteVoicePositionInfo info = getVoicePositionInfo(remoteLeftTv);
    Log.d(TAG, "left remote user >> pos=" + Arrays.toString(info.position));
    localSpatial.updateRemotePosition(uid, info);
    remoteLeftTv.setOnClickListener(v -> showRemoteUserSettingDialog(uid));
} else if (remoteRightTv.getTag() == null) {
    remoteRightTv.setTag(uid);
    remoteRightTv.setVisibility(View.VISIBLE);
    remoteRightTv.setText(uid + "");
    localSpatial.updateRemotePosition(uid, getVoicePositionInfo(remoteRightTv));
    remoteRightTv.setOnClickListener(v -> showRemoteUserSettingDialog(uid));
}
```

**Kotlin**
```kotlin
// Update local user position
val pos = getVoicePosition(localIv)
val forward = floatArrayOf(1.0F, 0.0F, 0.0F)
val right = floatArrayOf(0.0F, 1.0F, 0.0F)
val up = floatArrayOf(0.0F, 0.0F, 1.0F)
localSpatial.updateSelfPosition(pos, forward, right, up)

// Update the remote user's position
if (remoteLeftTv.tag == null) {
    remoteLeftTv.tag = uid
    remoteLeftTv.visibility = View.VISIBLE
    remoteLeftTv.text = "$uid"
    val info = getVoicePositionInfo(remoteLeftTv)
    Log.d(TAG, "left remote user >> pos=" + Arrays.toString(info.position))
    localSpatial.updateRemotePosition(uid, info)
    remoteLeftTv.setOnClickListener { showRemoteUserSettingDialog(uid) }
} else if (remoteRightTv.tag == null) {
    remoteRightTv.tag = uid
    remoteRightTv.visibility = View.VISIBLE
    remoteRightTv.text = "$uid"
    localSpatial.updateRemotePosition(uid, getVoicePositionInfo(remoteRightTv))
    remoteRightTv.setOnClickListener { showRemoteUserSettingDialog(uid) }
}
```


### Set spatial audio parameters

To set spatial audio parameters for the remote user or media player, call `setRemoteUserSpatialAudioParams` or `setSpatialAudioParams`. To implement specific sound effects, refer to the following parameter settings:

- Air attenuation effect

    Set `enable_air_absorb` to `true` and set `speaker_attenuation` to the desired sound attenuation coefficient.

- Sound blur effect

    Set `enable_blur` to `true`

**Java**
```java
// Enable air absorption effect
spatialAudioParams.enable_air_absorb = true;

// Enable sound blur effect
spatialAudioParams.enable_blur = true;

// Set spatial audio parameters for the media player
engine.setRemoteUserSpatialAudioParams(uid, spatialAudioParams);
```

**Kotlin**
```kotlin
// Enable air absorption effect
spatialAudioParams.enable_air_absorb = true

// Enable sound blur effect
spatialAudioParams.enable_blur = true

// Set spatial audio parameters for the media player
engine.setRemoteUserSpatialAudioParams(uid, spatialAudioParams)
```


### Set up sound isolation (optional)

To define a sound isolation zone and set a sound attenuation coefficient call `setZones`. This feature simulates the real-world effect of sound attenuation when the sound source is inside a sound isolation zone while the receiver is outside or vice versa. It mimics how sound behaves when encountering obstacles like building partitions.

Optionally, call `setRemoteAudioAttenuation` or `setPlayerAttenuation` to set the sound attenuation properties for the user and media player, respectively, and specify whether to use this setting to forcefully override the sound attenuation factor in `setZones`.

**Java**
```java
SpatialAudioZone mediaPlayerLeftZone = new SpatialAudioZone();
mediaPlayerLeftZone.zoneSetId = 1;
mediaPlayerLeftZone.audioAttenuation = 1f;
float[] voicePosition = getVoicePosition(zoneTv);
float[] viewRelativeSizeInAxis = getViewRelativeSizeInAxis(zoneTv);
mediaPlayerLeftZone.position = new float[]{voicePosition[0], voicePosition[1], 0};
mediaPlayerLeftZone.forward = new float[]{1.f, 0, 0};
mediaPlayerLeftZone.right = new float[]{0, 1.f, 0};
mediaPlayerLeftZone.up = new float[]{0, 0, 1.f};
mediaPlayerLeftZone.forwardLength = viewRelativeSizeInAxis[1];
mediaPlayerLeftZone.rightLength = viewRelativeSizeInAxis[0];
mediaPlayerLeftZone.upLength = AXIS_MAX_DISTANCE;
localSpatial.setZones(new SpatialAudioZone[]{mediaPlayerLeftZone});

localSpatial.setRemoteAudioAttenuation(uid, 0.5f, false);
localSpatial.setPlayerAttenuation(mediaPlayer.getMediaPlayerId(), 0.5f, false);
```

**Kotlin**
```kotlin
val mediaPlayerLeftZone = SpatialAudioZone().apply {
    zoneSetId = 1
    audioAttenuation = 1f
    val voicePosition = getVoicePosition(zoneTv)
    val viewRelativeSizeInAxis = getViewRelativeSizeInAxis(zoneTv)
    position = floatArrayOf(voicePosition[0], voicePosition[1], 0f)
    forward = floatArrayOf(1f, 0f, 0f)
    right = floatArrayOf(0f, 1f, 0f)
    up = floatArrayOf(0f, 0f, 1f)
    forwardLength = viewRelativeSizeInAxis[1]
    rightLength = viewRelativeSizeInAxis[0]
    upLength = AXIS_MAX_DISTANCE
}

localSpatial.setZones(arrayOf(mediaPlayerLeftZone))

localSpatial.setRemoteAudioAttenuation(uid, 0.5f, false)
localSpatial.setPlayerAttenuation(mediaPlayer.mediaPlayerId, 0.5f, false)
```


### Set headphone equalization (optional)

To optimize the audio experience, call `setHeadphoneEQPreset` to choose a preset headphone equalizer.

If the preset values do not provide the desired effect, call `setHeadphoneEQParameters` to self-adjust headphone equalization. After you execute this method, the preset values set by `setHeadphoneEQPreset` are overwritten.

**Java**
```java
engine.setHeadphoneEQPreset(io.agora.rtc2.Constants.HEADPHONE_EQUALIZER_OVEREAR);
engine.setHeadphoneEQParameters(10, 10);
```

**Kotlin**
```kotlin
engine.setHeadphoneEQPreset(io.agora.rtc2.Constants.HEADPHONE_EQUALIZER_OVEREAR)
engine.setHeadphoneEQParameters(10, 10)
```


### Pause or turn off spatial audio

During a session, pause or turn off spatial audio as follows.

#### Pause spatial audio for a remote user
To disable a remote user's spatial audio, or to remove a remote user who has exited the channel, call `removeRemotePosition` to delete the user's spatial position information and save computing resources.

> ⚠️ **Caution**
> When a remote user leaves the channel, call `removeRemotePosition` to delete the user's spatial position information. Otherwise, the local user may not be able to hear the spatial audio from other remote users.

To restore the user's spatial audio, call `updateRemotePosition` to reset the remote user's position information.

**Java**
```java
// Pause spatial audio for a specific remote user locally
localSpatial.removeRemotePosition(uid);

// Resume spatial audio for a specific remote user locally
localSpatial.updateRemotePosition(uid, getVoicePositionInfo(remoteLeftTv));
```

**Kotlin**
```kotlin
// Pause spatial audio for a specific remote user locally
localSpatial.removeRemotePosition(uid)

// Resume spatial audio for a specific remote user locally
localSpatial.updateRemotePosition(uid, getVoicePositionInfo(remoteLeftTv))
```


#### Pause spatial audio for all remote users

If you do not want to continue to experience local spatial audio, call `clearRemotePositions` to delete the spatial position information of all remote users.

> ⚠️ **Caution**
> Calling this method prevents the local user from hearing the audio of all remote users. Agora recommends using this method with caution.

To resume hearing the remote user's audio later, call `muteAllRemoteAudioStreams(false)` to subscribe to the remote audio streams again.

**Java**
```java
// Remove all remote users' spatial positions
localSpatial.clearRemotePositions();

// Resume subscribing to remote users' audio streams
localSpatial.muteAllRemoteAudioStreams(false);
```

**Kotlin**
```kotlin
// Remove all remote users' spatial positions
localSpatial.clearRemotePositions()

// Resume subscribing to remote users' audio streams
localSpatial.muteAllRemoteAudioStreams(false)
```


#### Pause the local user's spatial audio

To unpublish the local audio stream, call `muteLocalAudioStream(true)`.

To enable spatial audio again, call the same method with the parameter set to `false`.

**Java**
```java
// Mute the local audio stream
localSpatial.muteLocalAudioStream(true);

// Unmute the local audio stream
localSpatial.muteLocalAudioStream(false);
```

**Kotlin**
```kotlin
// Mute the local audio stream
localSpatial.muteLocalAudioStream(true)

// Unmute the local audio stream
localSpatial.muteLocalAudioStream(false)
```


#### Turn off spatial audio

To turn off spatial audio, call the `enableSpatialAudio` method of the `RtcEngine` instance and set the parameter to `false`. This resets all settings related to spatial audio.

To enable spatial audio again, call this method again and set the parameter to `true`, and then call the relevant APIs again to set the spatial audio effect.

Call the `destroy` method of the `ILocalSpatialAudioEngine` class to free up resources.

**Java**
```java
// Disable spatial audio
engine.enableSpatialAudio(false);

// Destroy the ILocalSpatialAudioEngine object
ILocalSpatialAudioEngine.destroy();
```

**Kotlin**
```kotlin
// Disable spatial audio
engine.enableSpatialAudio(false)

// Destroy the ILocalSpatialAudioEngine object
ILocalSpatialAudioEngine.destroy()
```


## Reference
This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Application use-cases

#### Social Chat

In the Voice Chat Room app, user avatars are arranged in a grid, each assigned specific coordinates and a direction. During interaction, the volume and direction of each user's voice correspond to their location. As you drag your avatar across the screen, the volume of another user decreases as you move away and disappears when you exceed a certain distance, simulating real-world sound propagation.

<div style={{ width: '33%' , height : '33%' }}>
  ![Social Chat](https://docs-md.agora.io/images/video-sdk/voice-chatroom-social-spatial.png)
</div>

#### Games & Metaverse

In 3D environments like games and the Metaverse, spatial audio technology can enhance experiences in the following ways:

<table>
  <tr>
    <td><strong>Audio Blurring</strong></td>
    <td>
      Enable audio blurring for specific users or media. 
      
      For example, in a coffee shop, use this to create a "whispering" effect where other users hear muffled conversations.
    </td>
  </tr>
  <tr>
    <td><strong>Range Audio</strong></td>
    <td>
      Set audio reception range based on the scene. The farther the sound travels, the fainter it becomes. Adjust the attenuation factor for different effects:
      <ul>
        <li><strong>Jungle scene:</strong> Set to 0.9 for fast sound decay.</li>
        <li><strong>Plain scene:</strong> Set to 0.1 for slower sound decay over a longer distance.</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td><strong>Ultra-Realistic Space</strong></td>
    <td>
      Assign virtual characters 3D coordinates for a realistic spatial experience, including:
      <ul>
        <li>Avatar coordinates (x, y, z). The coordinates of the avatar in 3D space.</li>
        <li>Virtual character face orientation coordinates (x, y, z): The face orientation coordinates of the virtual character in the scene.</li>
        <li>Virtual character head top orientation coordinates (x, y, z): Coordinates of the virtual character's head top orientation, which can be combined with the face orientation coordinates to determine the virtual character's actions and postures. For example, when the virtual character lies flat, the face is facing the sky and the top of the head is facing forward.</li>
        <li>Coordinate system (left or right-handed). Whether the rectangular coordinate system uses a left-handed coordinate system or a right-handed coordinate system. Only one coordinate system can be used in a scene.</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td><strong>Sound Insulation Area</strong></td>
    <td>
      Create sound-insulated zones with customized sound attenuation. When a receiver outside the area listens to the sound source in the area, they experience the attenuation effect of the sound in the real environment on encountering the building partition.
      
      For example, in a KTV scene, sounds from inside a box can be faintly heard from outside. Opening the door instantly amplifies the sound.
    </td>
  </tr>
</table>

![Games & Metaverse](https://docs-md.agora.io/images/video-sdk/gmes-scenario-spatial.png)

#### Online Meetings

In virtual meetings, spatial audio can arrange users around the host, with each microphone having directional sound. This setup mimics a real-world conference room, providing a more immersive and less tiring experience compared to traditional online meetings.

![Online Meetings](https://docs-md.agora.io/images/video-sdk/online-spatial-scenario.png)

### Sample project

Agora provides the [SpatialSound](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample-Audio/app/src/main/java/io/agora/api/example/examples/advanced/SpatialSound.java) open-source sample project for your reference. Download the project or view the source code for a more detailed example.

### API reference

- [`ILocalSpatialAudioEngine`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ilocalspatialaudioengine.html#class_ilocalspatialaudioengine)
    - [`initialize`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ilocalspatialaudioengine.html#api_ilocalspatialaudioengine_initialize)
    - [`muteAllRemoteAudioStreams`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ibasespatialaudioengine_muteallremoteaudiostreams)
    - [`setMaxAudioRecvCount`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ibasespatialaudioengine_setmaxaudiorecvcount)
    - [`setAudioRecvRange`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ibasespatialaudioengine_setaudiorecvrange)
    - [`setDistanceUnit`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ibasespatialaudioengine_setdistanceunit)
    - [`updateSelfPosition`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ibasespatialaudioengine_updateselfposition)
    - [`updateRemotePosition`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ilocalspatialaudioengine.html#api_ilocalspatialaudioengine_updateremoteposition)
    - [`updatePlayerPositionInfo`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ibasespatialaudioengine_updateplayerpositioninfo)
    - [`clearRemotePositions`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ilocalspatialaudioengine.html#api_ilocalspatialaudioengine_clearremotepositions)
    - [`setRemoteAudioAttenuation`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ilocalspatialaudioengine_setremoteaudioattenuation)
    - [`setZones`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ibasespatialaudioengine_setzones)
    - [`setPlayerAttenuation`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ibasespatialaudioengine.html#api_ibasespatialaudioengine_setplayerattenuation)
    - [`destroy`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ilocalspatialaudioengine.html#api_ilocalspatialaudioengine_release)

- [`IMediaPlayer`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#class_imediaplayer)
    - [`setSpatialAudioParams`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_imediaplayer_setspatialaudioparams)

- [`RTCEngine`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#class_irtcengine)
    - [`enableSpatialAudio`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_enablespatialaudio)
    - [`muteLocalAudioStream`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_mutelocalaudiostream)
    - [`setHeadphoneEQParameters`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setheadphoneeqparameters)
    - [`setHeadphoneEQPreset`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setheadphoneeqpreset)
    - [`setRemoteUserSpatialAudioParams`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setremoteuserspatialaudioparams)