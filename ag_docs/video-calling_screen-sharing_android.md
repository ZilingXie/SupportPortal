---
title: Screen sharing
description: Implement key workflow steps required to develop a fully functional video
  calling app
sidebar_position: 1
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/screen-sharing?platform=android
exported_on: '2026-01-20T05:56:51.768693Z'
exported_file: screen-sharing_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/screen-sharing?platform=android)

# Screen sharing

During Video Calling sessions, hosts use the screen sharing feature in the Agora Video SDK to share their screen content with other users or viewers in the form of a video stream. Screen sharing is typically used in the following use-cases:

| Use-case | Description |
|:---|:---------------|
| Online education | Teachers share their slides, software, or other teaching materials with students for classroom demonstrations. |
| Game live broadcast | Hosts share their game footage with the audience. |
| Interactive live broadcast | Anchors share their screens and interact with the audience. |
| Video conferencing | Meeting participants share the screen to show a presentation or documents. |
| Remote control | A controlled terminal displays its desktop on the master terminal. |

Agora screen sharing offers the following advantages:

- **Ultra HD quality experience**: Supports Ultra HD video (4K resolution, 60 FPS frame rate), giving users a smoother, high-definition, ultimate picture experience.
- **Multi-app support**: Compatible with many mainstream apps such as WPS Office, Microsoft Office Power Point, Visual Studio Code, Adobe Photoshop, Windows Media Player, and Scratch. This makes it convenient for users to directly share specific apps.
- **Multi-device support**: Supports multiple devices sharing at the same time. Screen sharing is compatible with Windows 8 systems, devices without independent graphics cards, dual graphics card devices, and external screen devices.
- **Multi-platform adaptation**: Supports iOS, Android, macOS, Windows, Web, Unity, Flutter, React Native, Unreal Engine, and other platforms.
- **High security**: Supports sharing only a single app or part of the screen. Also supports blocking specified app windows, effectively ensuring user information security.

This page shows you how to implement screen sharing in your app.

## Understand the tech

The screen sharing feature provides the following screen sharing modes for use in various use-cases:

**Screen sharing use-cases**

![Screen Sharing Functionality](https://docs-md.agora.io/images/video-sdk/screen-sharing-functionality.png)

- **Share the entire screen**: Share your entire screen, including all the information on the screen. This feature supports collecting and sharing information from two screens at the same time.
- **Share an app window**: If you don't want to share the entire screen with other users, you can share only the area within an app window.
- **Share a designated screen area**: If you only want to share a portion of the screen or app window, you can set a sharing area when starting screen sharing.

Screen sharing modes are available on different platforms as follows:

- **Desktop** (Windows and macOS): Supports all screen sharing features listed above.

- **Mobile** (Android and iOS): Only supports sharing the entire screen.

## Prerequisites

- On the Android platform, please ensure that the user has granted **screen capture** permission to the app.

- Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Set up your project

## Implement screen sharing


This section introduces how to implement screen sharing in your project. The basic API call sequence is shown in the figure below:

**API call sequence**

![API call sequence](https://docs-md.agora.io/images/video-sdk/screen-sharing-android-ios.svg)

Choose one of the following methods to enable screen sharing according to your use-case:

- Call `startScreenCapture` before joining the channel, then call `joinChannel [2/2]` to join the channel and set `publishScreenCaptureVideo` to **true** to start screen sharing.

- Call `startScreenCapture` after joining the channel, then call `updateChannelMediaOptions` to update the channel media options and set `publishScreenCaptureVideo` to **true** to start screen sharing.

The flow diagram and implementation steps in this article demonstrate the first use-case.

### Integrate screen sharing plug-in

Screen sharing in the Agora Video SDK is implemented through a plug-in. You can automatically integrate the plug-in through Maven Central or manual import of the **aar** file.

**Automatic integration**
When integrating the SDK through Maven Central, add dependencies by modifying the `dependencies` field in the `/Gradle Scripts/build.gradle(Module: <projectname>.app)` file as follows:

```java
dependencies {
    // Replace x.y.z in the following code with the specific SDK version number. You can get the latest version number from the release notes.
    def agora_sdk_version = "x.y.z"
    // If the value above contains $ signs, use "" instead of ''.

    // Choose one of the following blocks:
    // Integration solution 1
    implementation "io.agora.rtc:full-rtc-basic:\${agora_sdk_version}"
    implementation "io.agora.rtc:full-screen-sharing:\${agora_sdk_version}"
    implementation "io.agora.rtc:screen-capture:\${agora_sdk_version}"

    // Integration solution 2
    implementation "io.agora.rtc:full-sdk:\${agora_sdk_version}"
    implementation "io.agora.rtc:full-screen-sharing:\${agora_sdk_version}"
}
```

**Manual integration**
1. Copy the `AgoraScreenShareExtension.aar` file from the downloaded SDK to the `/app/libs/` directory.

1. Add the following line to the `dependencies` node of the `/app/build.gradle` file to support importing `aar` files:

    ```java
    implementation fileTree(dir: "libs", include: ["*.jar","*.aar"])
    ```

1. Ensure that the file `libagora_screen_capture_extension.so` exists in the `jniLibs` folder of your project. If it does not, copy it manually from the downloaded SDK folder.

1. Add the following code to the `/Gradle Scripts/build.gradle(Module: <projectname>.app` file to specify the location of the JNI library:

    ```java
    android {
          // ...
          sourceSets {
            main {
              jniLibs.srcDirs = ['src/main/jniLibs']
            }
          }
        }
    ```


### Set up the audio scenario

Call `setAudioScenario` and set the audio scenario to `AUDIO_SCENARIO_GAME_STREAMING` (high-quality scenario) to improve the success rate of capturing system audio during screen sharing. This step is optional.

### Enable screen capture

Call `startScreenCapture` to start capturing the screen and set the following parameters according to your application scenario:

- `captureVideo`: Whether to capture system video during screen sharing.
- `captureAudio`: Whether to capture system audio during screen sharing.
- `audioCaptureParameters`:
    - `sampleRate`: Audio sample rate (Hz). The default value is 16000.
    - `channels`: The number of audio channels. The default value is 2.
    - `captureSignalVolume`: The volume of the captured system audio.
    - `allowCaptureCurrentApp`: Whether to capture audio from the current app.
- `videoCaptureParameters`:
    - `width`: Specifies the width in pixels of the video encoding resolution. The default value is 1280.
    - `height`: Specifies the height in pixels of the video encoding resolution. The default value is 720. 
    - `frameRate`: Video encoding frame rate (FPS). The default value is 15.
    - `bitrate`: Video encoding bitrate (Kbps).
    - `contentHint`: Content type of screen sharing video. Choose from the following:
        - `SCREEN_CAPTURE_CONTENT_HINT_NONE`: (Default) No content hint.
        - `SCREEN_CAPTURE_CONTENT_HINT_MOTION`: Motion-intensive content. Choose this option if you prefer smoothness or when you are sharing a video clip, movie, or video game.
        - `SCREEN_CAPTURE_CONTENT_HINT_DETAILS`: Motionless content. Choose this option if you prefer sharpness or when you are sharing a picture, PowerPoint slides, or texts.

**Java**
```java
// Set parameters for screen capture
screenCaptureParameters.captureVideo = true;
screenCaptureParameters.videoCaptureParameters.width = 720;
screenCaptureParameters.videoCaptureParameters.height = (int) (720 * 1.0f / metrics.widthPixels * metrics.heightPixels);
screenCaptureParameters.videoCaptureParameters.framerate = DEFAULT_SHARE_FRAME_RATE;
screenCaptureParameters.captureAudio = screenAudio.isChecked();
screenCaptureParameters.audioCaptureParameters.captureSignalVolume = screenAudioVolume.getProgress();
engine.startScreenCapture(screenCaptureParameters);
```

**Kotlin**
```kotlin
// Set parameters for screen capture
screenCaptureParameters.captureVideo = true
screenCaptureParameters.videoCaptureParameters.width = 720
screenCaptureParameters.videoCaptureParameters.height = (720 * 1.0f / metrics.widthPixels * metrics.heightPixels).toInt()
screenCaptureParameters.videoCaptureParameters.framerate = DEFAULT_SHARE_FRAME_RATE
screenCaptureParameters.captureAudio = screenAudio.isChecked
screenCaptureParameters.audioCaptureParameters.captureSignalVolume = screenAudioVolume.progress
engine.startScreenCapture(screenCaptureParameters)
```


### Publish a screen sharing video stream in a channel

Call `joinChannel` [2/2] to join the channel. Set the `options` parameter to publish the captured screen sharing video stream as follows:

**Java**
```java
ChannelMediaOptions options = new ChannelMediaOptions();
options.clientRoleType = Constants.CLIENT_ROLE_BROADCASTER;
options.autoSubscribeVideo = true;
options.autoSubscribeAudio = true;
// Do not publish camera-captured video
options.publishCameraTrack = false;
// Do not publish microphone-captured audio
options.publishMicrophoneTrack = false;
// Publish screen-captured video in the channel
options.publishScreenCaptureVideo = true;
// Publish screen-captured audio in the channel
options.publishScreenCaptureAudio = true;
// Join the channel with the channel media options set above
int res = engine.joinChannel(accessToken, channelId, 0, options);
```

**Kotlin**
```kotlin
val options = ChannelMediaOptions().apply {
    clientRoleType = Constants.CLIENT_ROLE_BROADCASTER
    autoSubscribeVideo = true
    autoSubscribeAudio = true
    // Do not publish camera-captured video
    publishCameraTrack = false
    // Do not publish microphone-captured audio
    publishMicrophoneTrack = false
    // Publish screen-captured video in the channel
    publishScreenCaptureVideo = true
    // Publish screen-captured audio in the channel
    publishScreenCaptureAudio = true
}

// Join the channel with the channel media options set above
val res = engine.joinChannel(accessToken, channelId, 0, options)
```


### Set up a screen sharing scene (Optional)

Call the `setScreenCaptureScenario` method to set the screen sharing scenario and choose the `screenScenario` that best fits your application from the following:

- `SCREEN_SCENARIO_DOCUMENT`: Prioritizes video quality for screen sharing with reduced latency for the receiver.
- `SCREEN_SCENARIO_GAMING`: Focuses on achieving smooth screen sharing for gaming scenarios.
- `SCREEN_SCENARIO_VIDEO`: Optimizes the screen sharing experience for video playback by enhancing smoothness.

**Java**
```java
engine.setScreenCaptureScenario(Constants.SCREEN_SCENARIO_VIDEO);
```

**Kotlin**
```kotlin
engine.setScreenCaptureScenario(Constants.SCREEN_SCENARIO_VIDEO)
```


### Update screen sharing (Optional)

If you want to update the screen sharing parameters, such as the video encoding resolution, frame rate, or bitrate, call `updateScreenCaptureParameters` to modify the parameter values. 

**Java**
```java
ScreenCaptureParameters screenCaptureParameters = new ScreenCaptureParameters();

// Set new screen sharing parameters
screenCaptureParameters.captureVideo = true;
screenCaptureParameters.videoCaptureParameters.width = 1280;
screenCaptureParameters.videoCaptureParameters.height = 720;
screenCaptureParameters.videoCaptureParameters.framerate = 30;

// Update the screen sharing parameters
engine.updateScreenCaptureParameters(screenCaptureParameters);
```

**Kotlin**
```kotlin
val screenCaptureParameters = ScreenCaptureParameters()

// Set new screen sharing parameters
screenCaptureParameters.captureVideo = true
screenCaptureParameters.videoCaptureParameters.width = 1280
screenCaptureParameters.videoCaptureParameters.height = 720
screenCaptureParameters.videoCaptureParameters.framerate = 30

// Update the screen sharing parameters
engine.updateScreenCaptureParameters(screenCaptureParameters)
```


### Stop screen sharing

Call `stopScreenCapture` to stop screen sharing within the channel.

**Java**
```java
engine.stopScreenCapture();
```

**Kotlin**
```kotlin
engine.stopScreenCapture()
```


### Limitations

Be aware of the following limitations:

- After turning on screen sharing, Agora uses the resolution of the screen sharing video stream as the billing standard. Please see [Pricing](https://docs-md.agora.io/en/video-calling/overview/pricing.md) for details. The default resolution is 1280 × 720, but you can adjust it according to your business needs.

- Due to Android performance limitations, screen sharing does not support Android TV.

- When using screen sharing on Huawei mobile phones, do not adjust the video encoding resolution of the screen sharing stream during the sharing process to avoid crashes.

- Some Xiaomi phones do not support capturing system audio during screen sharing.

- On Android 9 and later, to avoid system termination when the app is backed up, it is recommended to add the foreground service permission: `android.permission.FOREGROUND_SERVICE` to the `/app/Manifests/AndroidManifest.xml` file.

- Screen capture is only available for Android API level 21 (Android 5) or later. On earlier versions the SDK reports error codes `ERR_SCREEN_CAPTURE_PERMISSION_DENIED(16)` and `ERR_ SCREEN_CAPTURE_SYSTEM_NOT_SUPPORTED(2)`.

- Capturing system audio is only available for Android API level 29 (Android 10) or later. On earlier versions, the SDK reports the error code `ERR_SCREEN_CAPTURE_SYSTEM_AUDIO_NOT_SUPPORTED(3)`.

## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Sample project

Agora provides an open-source Android [sample project](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/ScreenSharing.java) on GitHub. Download and explore this project for a more detailed example.

### API reference

- [`startScreenCapture`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_startscreencapture)
- [`ScreenCaptureParameters`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_screencaptureparameters2.html)
- [`updateScreenCaptureParameters`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_updatescreencaptureparameters)
- [`stopScreenCapture`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_stopscreencapture)
- <Link to="{{Global.API_REF_ANDROID_ROOT}}/class_irtcengine.html#api_irtcengine_setscreencapturescenario">`setScreenCaptureScenario
`</Link>