---
title: Quickstart
description: Rapidly develop and easily enhance your social, work, and educational
  apps with face-to-face interaction.
sidebar_position: 2
platform: android
exported_from: https://docs.agora.io/en/video-calling/get-started/get-started-sdk?platform=android
exported_on: '2026-01-20T05:58:09.488678Z'
exported_file: get-started-sdk_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/get-started/get-started-sdk?platform=android)

# Quickstart

This page provides a step-by-step guide on how to create a basic Video Calling app using the Agora Video SDK.

## Understand the tech

To start a Video Calling session, implement the following steps in your app:

- **Initialize the Agora Engine**: Before calling other APIs, create and initialize an Agora Engine instance.

- **Join a channel**: Call methods to create and join a channel.

- **Send and receive audio and video**: All users can publish streams to the channel and subscribe to audio and video streams published by other users in the channel.

![Video calling workflow](https://docs-md.agora.io/images/video-sdk/video-call.svg)

## Prerequisites

- [Android Studio](https://developer.android.com/studio) 4.2 or higher.
- Android SDK API Level 21 or higher.
- Two mobile devices running Android 5.0 or higher.


- A camera and a microphone

- A valid Agora account and project. Please refer to [Agora account management](https://docs-md.agora.io/en/video-calling/get-started/manage-agora-account.md) for details.

## Set up your project

This section shows you how to set up your Android project and install the Agora Video SDK.


**Create a new project**
1. Create a [new project](https://developer.android.com/studio/projects/create-project).

    1. Open Android Studio and select **File > New > New Project...**.
    1. Select **Phone and Tablet** > **Empty Activity** and click **Next**.
    1. Set the project name and storage path. 
    1. Select **Java** or **Kotlin** as the language, and click **Finish** to create the Android project.
    
    > ⚠️ **Note**
    > After you create a project, Android Studio automatically starts gradle sync. Ensure that the synchronization is successful before proceeding to the next step.

**Add to an existing project**
1. Add a new activity to your project.

   1. Open your project in Android Studio.
   1. Right-click on the `app/src/main/java/<your.package.name>` folder.
   1. Select **New → Activity → Empty Activity**.
   1. Enter an activity name and click **Finish**. 
       This guide uses `MainActivity` as the activity name in the sample code. Replace it with your activity name where required.


2. Add a layout file for your activity. 

    Set up two container elements in your activity to display local and remote video streams. Refer to [Create a user interface](#create-a-user-interface) to get a bare bones sample layout.

### Install the SDK

Use either of the following methods to add Video SDK to your project.

**Maven Central**
1. Open the `settings.gradle` file in the project's root directory and add the Maven Central dependency, if it doesn't already exist:

    ```groovy
    repositories {
       mavenCentral()
    }
    ```
    > ℹ️ **Info**
    > If your Android project uses <a href="https://docs.gradle.org/current/userguide/declaring_repositories.html#sub:centralized-repository-declaration">dependencyResolutionManagement</a>, the method of adding the Maven Central dependency may differ.

1. To integrate the Video SDK into your Android project, add the following to the `dependencies` block in your project module `build.gradle` file:

    - Groovy `build.gradle`

        ```json
        implementation 'io.agora.rtc:full-sdk:x.y.z'
        ```

    - Kotlin `build.gradle.kts`

        ```kotlin
        implementation("io.agora.rtc:full-sdk:x.y.z")
        ```

    Replace `x.y.z` with the specific SDK version number, such as `4.5.0`.

    > ℹ️ **Info**
    > To get the latest version number, check the [Release notes](https://docs-md.agora.io/en/video-calling/overview/release-notes.md). To integrate the Lite SDK, use `io.agora.rtc:lite-sdk` instead.

1. Prevent code obfuscation

    Open the `/app/proguard-rules.pro` file and add the following lines to prevent the Video SDK code from being obfuscated:

    ```java
    -keep class io.agora.** { *; }
       -dontwarn io.agora.**
    ```

**Manual integration**
1. Download the latest version of Video SDK from the  the [SDKs](https://docs-md.agora.io/en/sdks_android.md) page and unzip it.

1. Open the unzipped file and copy the following files or subfolders to your project path.

    | File or folder                | Project path        |
    | :---------------------------- | :----------------------- |
    | `agora-rtc-sdk.jar` file      | `/app/libs/`             |
    | `arm64-v8a` folder            | `/app/src/main/jniLibs/` |
    | `armeabi-v7a` folder          | `/app/src/main/jniLibs/` |
    | `x86` folder                  | `/app/src/main/jniLibs/` |
    | `x86_64` folder               | `/app/src/main/jniLibs/` |
    | `high_level_api` in `include` folder  | `/app/src/main/jniLibs/`  |

1. Select the file `/app/libs/agora-rtc-sdk.jar` in the left navigation bar of Android Studio project files, right-click, and select **add as a library** from the drop-down menu.

1. Prevent code obfuscation

    Open the `/app/proguard-rules.pro` file and add the following lines to prevent the Video SDK code from being obfuscated:

    ```java
    -keep class io.agora.** { *; }
       -dontwarn io.agora.**
    ```

## Implement Video Calling

This section guides you through the implementation of basic real-time audio and video interaction in your app.

The following figure illustrates the essential steps:

**Quick start sequence**

![](https://docs-md.agora.io/images/video-sdk/quick-start-sequence.svg)

This guide includes [complete sample code](#complete-sample-code) that demonstrates implementing basic real-time interaction. To understand the core API calls in the sample code, review the following implementation steps and use the code in your `MainActivity` file.

### Import Agora classes

Import the relevant Agora classes and interfaces:

**Java**
```java
import io.agora.rtc2.Constants;
import io.agora.rtc2.IRtcEngineEventHandler;
import io.agora.rtc2.RtcEngine;
import io.agora.rtc2.RtcEngineConfig;
import io.agora.rtc2.video.VideoCanvas;
import io.agora.rtc2.ChannelMediaOptions;
```

**Kotlin**
```kotlin
import io.agora.rtc2.Constants
import io.agora.rtc2.IRtcEngineEventHandler
import io.agora.rtc2.RtcEngine
import io.agora.rtc2.RtcEngineConfig
import io.agora.rtc2.video.VideoCanvas
import io.agora.rtc2.ChannelMediaOptions
```


### Initialize the engine

For real-time communication, initialize an `RtcEngine` instance and set up event handlers to manage user interactions within the channel. Use `RtcEngineConfig` to specify the application context, [App ID](https://docs-md.agora.io/en/video-calling/get-started/manage-agora-account.md), and custom [event handler](#subscribe-to--events), then call `RtcEngine.create(config)` to initialize the engine, enabling further channel operations. In your `MainActivity` file, add the following code:

**Java**
```java
// Fill in the app ID from Agora Console
private String myAppId = "<Your app ID>";
private RtcEngine mRtcEngine;

private void initializeAgoraVideoSDK() {
    try {
        RtcEngineConfig config = new RtcEngineConfig();
        config.mContext = getBaseContext();
        config.mAppId = myAppId;
        config.mEventHandler = mRtcEventHandler;
        mRtcEngine = RtcEngine.create(config);
    } catch (Exception e) {
        throw new RuntimeException("Error initializing RTC engine: " + e.getMessage());
    }
}
```

**Kotlin**
```kotlin
// Fill in the App ID obtained from the Agora Console
private val myAppId = "<Your app ID>"
private var mRtcEngine: RtcEngine? = null

private fun initializeRtcEngine() {
    try {
        val config = RtcEngineConfig().apply {
            mContext = applicationContext
            mAppId = myAppId
            mEventHandler = mRtcEventHandler
        }
        mRtcEngine = RtcEngine.create(config)
    } catch (e: Exception) {
        throw RuntimeException("Error initializing RTC engine: ${e.message\}")
    }
}
```


### Join a channel

To join a channel, call `joinChannel` with the following parameters:

- **Channel name**: The name of the channel to join. Clients that pass the same channel name join the same channel. If a channel with the specified name does not exist, it is created when the first user joins.

- **Authentication token**: A dynamic key that authenticates a user when the client joins a channel. In a production environment, you obtain a token from a [token server](https://docs-md.agora.io/en/video-calling/token-authentication/deploy-token-server.md) in your security infrastructure. For the purpose of this guide [Generate a temporary token](https://docs-md.agora.io/en/video-calling/get-started/manage-agora-account.md).

- **User ID**: A 32-bit signed integer that identifies a user in the channel. You can specify a unique user ID for each user yourself. If you set the user ID to `0` when joining a channel, the SDK generates a random number for the user ID and returns the value in the `onJoinChannelSuccess` callback. 

- **Channel media options**: Configure `ChannelMediaOptions` to define publishing and subscription settings, optimize performance for your specific use-case, and set optional parameters. 

For Video Calling, set the `channelProfile` to `CHANNEL_PROFILE_COMMUNICATION` and the `clientRoleType` to `CLIENT_ROLE_BROADCASTER`.

**Java**
```java
// Fill in the channel name
private String channelName = "<Your channel name>";
// Fill in the temporary token generated from Agora Console
private String token = "<Your token>";

private void joinChannel() {
    ChannelMediaOptions options = new ChannelMediaOptions();
    options.clientRoleType = Constants.CLIENT_ROLE_BROADCASTER;
    options.channelProfile = Constants.CHANNEL_PROFILE_COMMUNICATION;
    options.publishCameraTrack = true;
    options.publishMicrophoneTrack = true;
    mRtcEngine.joinChannel(token, channelName, 0, options);
}
```

**Kotlin**
```kotlin
// Fill in the channel name
private val channelName = "<Your channel name>"
// Fill in the temporary token generated from Agora Console
private val token = "<Your token>"

private fun joinChannel() {
    val options = ChannelMediaOptions().apply {
        clientRoleType = Constants.CLIENT_ROLE_BROADCASTER
        channelProfile = Constants.CHANNEL_PROFILE_COMMUNICATION
        publishMicrophoneTrack = true
        publishCameraTrack = true
    }
    mRtcEngine.joinChannel(token, channelName, 0, options)
}
```


### Subscribe to Video SDK events

The Video SDK provides an interface for subscribing to channel events. To use it, create an instance of `IRtcEngineEventHandler` and implement the event methods you want to handle.

> ℹ️ **Info**
> To ensure that you receive all Video SDK events, set the Agora Engine event handler before joining a channel.

**Java**
```java
private final IRtcEngineEventHandler mRtcEventHandler = new IRtcEngineEventHandler() {
    // Triggered when the local user successfully joins the specified channel.
    @Override
    public void onJoinChannelSuccess(String channel, int uid, int elapsed) {
        super.onJoinChannelSuccess(channel, uid, elapsed);
        showToast("Joined channel " + channel);
    }

    // Triggered when a remote user/host joins the channel.
    @Override
    public void onUserJoined(int uid, int elapsed) {
        super.onUserJoined(uid, elapsed);
        runOnUiThread(() -> {
            // Initialize and display remote video view for the new user.
            setupRemoteVideo(uid);
            showToast("User joined: " + uid); 
        });
    }

    // Triggered when a remote user/host leaves the channel.
    @Override
    public void onUserOffline(int uid, int reason) {
        super.onUserOffline(uid, reason);
        runOnUiThread(() -> {
            showToast("User offline: " + uid); 
        });
    }
};
```

**Kotlin**
```kotlin
private val mRtcEventHandler = object : IRtcEngineEventHandler() {
    override fun onJoinChannelSuccess(channel: String?, uid: Int, elapsed: Int) {
        super.onJoinChannelSuccess(channel, uid, elapsed)
        runOnUiThread {
            showToast("Joined channel $channel")
        }
    }
    override fun onUserJoined(uid: Int, elapsed: Int) {
        runOnUiThread {
            showToast("User joined: $uid")
        }
    }
    override fun onUserOffline(uid: Int, reason: Int) {
        super.onUserOffline(uid, reason)
        runOnUiThread {
            showToast("User offline: $uid")
        }
    }
}
```


### Enable the video module

Follow these steps to enable the video module:

1. Call `enableVideo` to enable the video module.
1. Call `startPreview` to enable local video preview.

**Java**
```java
private void enableVideo() {
    mRtcEngine.enableVideo();
    mRtcEngine.startPreview();
}
```

**Kotlin**
```kotlin
private fun enableVideo() {
    mRtcEngine?.apply {
        enableVideo()
        startPreview()
    }
}
```


### Display the local video

Call `setupLocalVideo` to initialize the local view and set the local video display properties.

**Java**
```java
private void setupLocalVideo() {
    FrameLayout container = findViewById(R.id.local_video_view_container);
    SurfaceView surfaceView = new SurfaceView(getBaseContext());
    container.addView(surfaceView);
    mRtcEngine.setupLocalVideo(new VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, 0));
}
```

**Kotlin**
```kotlin
/**
 * Initializes the local video view and sets the display properties.
 * This method adds a SurfaceView to the local video container and configures it.
 */
private fun setupLocalVideo() {
    val container: FrameLayout = findViewById(R.id.local_video_view_container)
    val surfaceView = SurfaceView(baseContext)
    container.addView(surfaceView)
    mRtcEngine.setupLocalVideo(VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, 0))
}
```


### Display remote video

When a remote user joins the channel, call `setupRemoteVideo` and pass in the remote user's `uid`, obtained from the `onUserJoined` callback, to display the remote video.

**Java**
```java
private void setupRemoteVideo(int uid) {
    FrameLayout container = findViewById(R.id.remote_video_view_container);
    SurfaceView surfaceView = new SurfaceView(getBaseContext());
    surfaceView.setZOrderMediaOverlay(true);
    container.addView(surfaceView);
    mRtcEngine.setupRemoteVideo(new VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, uid));
}
```

**Kotlin**
```kotlin
private fun setupRemoteVideo(uid: Int) {
    val container = findViewById<FrameLayout>(R.id.remote_video_view_container)
    val surfaceView = SurfaceView(baseContext).apply {
        setZOrderMediaOverlay(true)
    }
    container.addView(surfaceView)
    mRtcEngine.setupRemoteVideo(VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, uid))
}
```


### Handle permissions

To access the camera and microphone on Android devices, declare the necessary permissions in the app's manifest and ensure that the user grants these permissions when the app starts.

1. Open your project's `AndroidManifest.xml` file and add the following permissions before `<application>`:

    ```xml
    <uses-feature android:name="android.hardware.camera" android:required="false" />
    <!--Required permissions-->
    <uses-permission android:name="android.permission.INTERNET"/>
    <uses-permission android:name="android.permission.CAMERA"/>
    <uses-permission android:name="android.permission.RECORD_AUDIO"/>
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS"/>
    <!--Optional permissions-->
    <uses-permission android:name="android.permission.ACCESS_WIFI_STATE"/>
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>
    <uses-permission android:name="android.permission.BLUETOOTH"/>
    <!-- For devices running Android 12 (API level 32) or higher and integrating Agora Video SDK version v4.1.0 or lower, you also need to add the following permissions -->
    <uses-permission android:name="android.permission.BLUETOOTH_CONNECT"/>
    <!-- For Android 12.0 or higher, the following permissions are also required -->
    <uses-permission android:name="android.permission.READ_PHONE_STATE"/>
    <uses-permission android:name="android.permission.BLUETOOTH_SCAN"/>
    ```

1. Use the following code to handle runtime permissions in your Android app. The logic ensures that the necessary permissions are granted before starting Video Calling. In your `MainActivity` file, add the following code:

    **Java**
    ```java
    private static final int PERMISSION_REQ_ID = 22;
    
        private void requestPermissions() {
            ActivityCompat.requestPermissions(this, getRequiredPermissions(), PERMISSION_REQ_ID);
        }
        private boolean checkPermissions() {
            for (String permission : getRequiredPermissions()) {
                if (ContextCompat.checkSelfPermission(this, permission) != PackageManager.PERMISSION_GRANTED) {
                    return false;
                }
            }
            return true;
        }
        private String[] getRequiredPermissions() {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
                return new String[]{
                    Manifest.permission.RECORD_AUDIO,
                    Manifest.permission.CAMERA,
                    Manifest.permission.READ_PHONE_STATE,
                    Manifest.permission.BLUETOOTH_CONNECT
                };
            } else {
                return new String[]{
                    Manifest.permission.RECORD_AUDIO,
                    Manifest.permission.CAMERA
                };
            }
        }
        @Override
        public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
            super.onRequestPermissionsResult(requestCode, permissions, grantResults);
            if (requestCode == PERMISSION_REQ_ID && checkPermissions()) {
                startVideoCalling();
            }
        }
    ```

    **Kotlin**
    ```kotlin
    private val PERMISSION_REQ_ID = 22
    
        private fun requestPermissions() {
            ActivityCompat.requestPermissions(this, getRequiredPermissions(), PERMISSION_REQ_ID)
        }
    
        private fun checkPermissions(): Boolean {
            for (permission in getRequiredPermissions()) {
                if (ContextCompat.checkSelfPermission(this, permission) != PackageManager.PERMISSION_GRANTED) {
                    return false
                }
            }
            return true
        }
    
        private fun getRequiredPermissions(): Array<String> {
            return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                arrayOf(
                    Manifest.permission.RECORD_AUDIO,
                    Manifest.permission.CAMERA,
                    Manifest.permission.READ_PHONE_STATE,
                    Manifest.permission.BLUETOOTH_CONNECT
                )
            } else {
                arrayOf(
                    Manifest.permission.RECORD_AUDIO,
                    Manifest.permission.CAMERA
                )
            }
        }
    
        override fun onRequestPermissionsResult(
            requestCode: Int,
            permissions: Array<out String>,
            grantResults: IntArray
        ) {
            super.onRequestPermissionsResult(requestCode, permissions, grantResults)
            if (requestCode == PERMISSION_REQ_ID && checkPermissions()) {
                startVideoCalling()
            }
        }
    ```


### Start and close the app

When a user launches your app, start real-time interaction. When a user closes the app, stop the interaction.

1. In the `onCreate` callback, check whether the app has been granted the required permissions. If the permissions have not been granted, request the required permissions from the user. If permissions are granted, initialize `RtcEngine` and join a channel.

    **Java**
    ```java
    @Override
        protected void onCreate(Bundle savedInstanceState) {
            super.onCreate(savedInstanceState);
            setContentView(R.layout.activity_main);
            if (checkPermissions()) {
                startVideoCalling();
            } else {
                requestPermissions();
            }
        }
    ```

    **Kotlin**
    ```kotlin
    override fun onCreate(savedInstanceState: Bundle?) {
            super.onCreate(savedInstanceState)
            setContentView(R.layout.activity_main)
            if (checkPermissions()) {
                startVideoCalling()
            } else {
                requestPermissions()
            }
        }
    ```
    

2. When a user closes the app, or switches the app to the background, call `stopPreview` to stop the video preview and then call `leaveChannel` to leave the current channel and release all session-related resources.

    **Java**
    ```java
    private void cleanupAgoraEngine() {
            if (mRtcEngine != null) {
                mRtcEngine.stopPreview();
                mRtcEngine.leaveChannel();
                mRtcEngine = null;
            }
        }
    ```

    **Kotlin**
    ```kotlin
    private fun cleanupAgoraEngine() {
            mRtcEngine?.apply {
                stopPreview()
                leaveChannel()
            }
            mRtcEngine = null
        }
    ```


### Complete sample code

A complete code sample demonstrating the basic process of real-time interaction is provided for your reference. To use the sample code, copy the following lines into the `MainActivity` file in your project. Then, replace `<projectname>` in package `com.example.<projectname>` with your project's name.

**Complete sample code for real-time Video Calling**

**Java**

```java
package com.example.<projectname>;

import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.view.SurfaceView;
import android.widget.FrameLayout;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import io.agora.rtc2.ChannelMediaOptions;
import io.agora.rtc2.Constants;
import io.agora.rtc2.IRtcEngineEventHandler;
import io.agora.rtc2.RtcEngine;
import io.agora.rtc2.RtcEngineConfig;
import io.agora.rtc2.video.VideoCanvas;

public class MainActivity extends AppCompatActivity {
    private static final int PERMISSION_REQ_ID = 22;
    private String myAppId = "<Your app ID>";
    private String channelName = "<Your channel name>";
    private String token = "<Your token>";
    private RtcEngine mRtcEngine;

    private final IRtcEngineEventHandler mRtcEventHandler = new IRtcEngineEventHandler() {
        // Callback when successfully joining the channel
        @Override
        public void onJoinChannelSuccess(String channel, int uid, int elapsed) {
            super.onJoinChannelSuccess(channel, uid, elapsed);
            showToast("Joined channel " + channel);
        }
        // Callback when a remote user or host joins the current channel
        @Override
        public void onUserJoined(int uid, int elapsed) {
            super.onUserJoined(uid, elapsed);
            runOnUiThread(() -> {
                // When a remote user joins the channel, display the remote video stream for the specified uid
                setupRemoteVideo(uid);
                showToast("User joined: " + uid); // Show toast for user joining
            });
        }
        // Callback when a remote user or host leaves the current channel
        @Override
        public void onUserOffline(int uid, int reason) {
            super.onUserOffline(uid, reason);
            runOnUiThread(() -> {
                showToast("User offline: " + uid); // Show toast for user going offline
            });
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        if (checkPermissions()) {
            startVideoCalling();
        } else {
            requestPermissions();
        }
    }

    private void requestPermissions() {
        ActivityCompat.requestPermissions(this, getRequiredPermissions(), PERMISSION_REQ_ID);
    }

    private boolean checkPermissions() {
        for (String permission : getRequiredPermissions()) {
            if (ContextCompat.checkSelfPermission(this, permission) != PackageManager.PERMISSION_GRANTED) {
                return false;
            }
        }
        return true;
    }

    private String[] getRequiredPermissions() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
            return new String[]{
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.CAMERA,
                Manifest.permission.READ_PHONE_STATE,
                Manifest.permission.BLUETOOTH_CONNECT
            };
        } else {
            return new String[]{
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.CAMERA
            };
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQ_ID && checkPermissions()) {
            startVideoCalling();
        }
    }

    private void startVideoCalling() {
        initializeAgoraVideoSDK();
        enableVideo();
        setupLocalVideo();
        joinChannel();
    }

    private void initializeAgoraVideoSDK() {
        try {
            RtcEngineConfig config = new RtcEngineConfig();
            config.mContext = getBaseContext();
            config.mAppId = myAppId;
            config.mEventHandler = mRtcEventHandler;
            mRtcEngine = RtcEngine.create(config);
        } catch (Exception e) {
            throw new RuntimeException("Error initializing RTC engine: " + e.getMessage());
        }
    }

    private void enableVideo() {
        mRtcEngine.enableVideo();
        mRtcEngine.startPreview();
    }

    private void setupLocalVideo() {
        FrameLayout container = findViewById(R.id.local_video_view_container);
        SurfaceView surfaceView = new SurfaceView(getBaseContext());
        container.addView(surfaceView);
        mRtcEngine.setupLocalVideo(new VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, 0));
    }

    private void joinChannel() {
        ChannelMediaOptions options = new ChannelMediaOptions();
        options.clientRoleType = Constants.CLIENT_ROLE_BROADCASTER;
        options.channelProfile = Constants.CHANNEL_PROFILE_COMMUNICATION;
        options.publishCameraTrack = true;
        options.publishMicrophoneTrack = true;
        mRtcEngine.joinChannel(token, channelName, 0, options);
    }

    private void setupRemoteVideo(int uid) {
        FrameLayout container = findViewById(R.id.remote_video_view_container);
        SurfaceView surfaceView = new SurfaceView(getBaseContext());
        surfaceView.setZOrderMediaOverlay(true);
        container.addView(surfaceView);
        mRtcEngine.setupRemoteVideo(new VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, uid));
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        cleanupAgoraEngine();
    }

    private void cleanupAgoraEngine() {
        if (mRtcEngine != null) {
            mRtcEngine.stopPreview();
            mRtcEngine.leaveChannel();
            mRtcEngine = null;
        }
    }

    private void showToast(String message) {
        runOnUiThread(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT).show());
    }
}
```

**Kotlin**

```kotlin
package com.example.<projectname>

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.SurfaceView
import android.widget.FrameLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import io.agora.rtc2.ChannelMediaOptions
import io.agora.rtc2.Constants
import io.agora.rtc2.IRtcEngineEventHandler
import io.agora.rtc2.RtcEngine
import io.agora.rtc2.RtcEngineConfig
import io.agora.rtc2.video.VideoCanvas

class MainActivity : AppCompatActivity() {

    companion object {
        private const val PERMISSION_REQ_ID = 22
    }

    private val myAppId = "<Your app ID>"
    private val channelName = "<Your channel name>"
    private val token = "<Your token>"
    private var mRtcEngine: RtcEngine? = null

    private val mRtcEventHandler = object : IRtcEngineEventHandler() {
        override fun onJoinChannelSuccess(channel: String?, uid: Int, elapsed: Int) {
            super.onJoinChannelSuccess(channel, uid, elapsed)
            showToast("Joined channel $channel")
        }

        override fun onUserJoined(uid: Int, elapsed: Int) {
            super.onUserJoined(uid, elapsed)
            runOnUiThread {
                setupRemoteVideo(uid)
                showToast("User joined: $uid")
            }
        }

        override fun onUserOffline(uid: Int, reason: Int) {
            super.onUserOffline(uid, reason)
            runOnUiThread {
                showToast("User offline: $uid")
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (checkPermissions()) {
            startVideoCalling()
        } else {
            requestPermissions()
        }
    }

    private fun requestPermissions() {
        ActivityCompat.requestPermissions(this, getRequiredPermissions(), PERMISSION_REQ_ID)
    }

    private fun checkPermissions(): Boolean {
        return getRequiredPermissions().all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }
    }

    private fun getRequiredPermissions(): Array<String> {
        return if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
            arrayOf(
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.CAMERA,
                Manifest.permission.READ_PHONE_STATE,
                Manifest.permission.BLUETOOTH_CONNECT
            )
        } else {
            arrayOf(
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.CAMERA
            )
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQ_ID && checkPermissions()) {
            startVideoCalling()
        }
    }

    private fun startVideoCalling() {
        initializeAgoraVideoSDK()
        enableVideo()
        setupLocalVideo()
        joinChannel()
    }

    private fun initializeAgoraVideoSDK() {
        try {
            val config = RtcEngineConfig().apply {
                mContext = applicationContext
                mAppId = myAppId
                mEventHandler = mRtcEventHandler
            }
            mRtcEngine = RtcEngine.create(config)
        } catch (e: Exception) {
            throw RuntimeException("Error initializing RTC engine: ${\e.message\}")
        }
    }

    private fun enableVideo() {
        mRtcEngine?.apply {
            enableVideo()
            startPreview()
        }
    }

    private fun setupLocalVideo() {
        val container: FrameLayout = findViewById(R.id.local_video_view_container)
        val surfaceView = SurfaceView(baseContext)
        container.addView(surfaceView)
        mRtcEngine?.setupLocalVideo(VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, 0))
    }

    private fun joinChannel() {
        val options = ChannelMediaOptions().apply {
            clientRoleType = Constants.CLIENT_ROLE_BROADCASTER
            channelProfile = Constants.CHANNEL_PROFILE_COMMUNICATION
            publishMicrophoneTrack = true
            publishCameraTrack = true
        }
        mRtcEngine?.joinChannel(token, channelName, 0, options)
    }

    private fun setupRemoteVideo(uid: Int) {
        val container: FrameLayout = findViewById(R.id.remote_video_view_container)
        val surfaceView = SurfaceView(applicationContext).apply {
            setZOrderMediaOverlay(true)
            container.addView(this)
        }
        mRtcEngine?.setupRemoteVideo(VideoCanvas(surfaceView, VideoCanvas.RENDER_MODE_FIT, uid))
    }

    override fun onDestroy() {
        super.onDestroy()
        cleanupAgoraEngine()
    }

    private fun cleanupAgoraEngine() {
        mRtcEngine?.apply {
            stopPreview()
            leaveChannel()
        }
        mRtcEngine = null
    }

    private fun showToast(message: String) {
        runOnUiThread {
            Toast.makeText(this@MainActivity, message, Toast.LENGTH_SHORT).show()
        }
    }
}
```

> ℹ️ **Info**
> For the `myAppId` and `token` variables, replace the placeholders with the values you obtained from Agora Console. Ensure you enter the same `channelName` you used when generating the temporary token.

### Create a user interface

To connect the sample code to your existing UI, ensure that your XML layout includes the container UI element IDs used to [Display the local video](#display-the-local-video) and [Display remote video](#display-remote-video).

Alternatively, use the following sample code to generate a basic user interface. Replace the existing content in `/app/src/main/res/layout/activity_main.xml` with this code.

![UI design](https://docs-md.agora.io/images/video-sdk/quickstart-ui-android-design.png)

**Sample code to create the user interface**

```xml
<?xml version="1.0" encoding="utf-8"?>
<androidx.constraintlayout.widget.ConstraintLayout xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:app="http://schemas.android.com/apk/res-auto"
    xmlns:tools="http://schemas.android.com/tools"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    tools:context=".MainActivity">

    <FrameLayout
        android:id="@+id/local_video_view_container"
        android:layout_width="match_parent"
        android:layout_height="match_parent"
        android:background="@android:color/white" />

    <FrameLayout
        android:id="@+id/remote_video_view_container"
        android:layout_width="160dp"
        android:layout_height="160dp"
        android:layout_marginEnd="16dp"
        android:layout_marginTop="16dp"
        android:background="@android:color/darker_gray"
        app:layout_constraintEnd_toEndOf="parent"
        app:layout_constraintTop_toTopOf="parent" />

</androidx.constraintlayout.widget.ConstraintLayout>
```

## Test the sample code

Take the following steps to test the sample code:

1. In `MainActivity` update the values for `myAppId`, and `token` with values from Agora Console. Fill in the same `channelName` you used to generate the token.

1. Enable developer options on your Android test device. Turn on USB debugging, connect the Android device to your development machine through a USB cable, and check that your device appears in the Android device options.

1. In Android Studio, click ![](https://docs-md.agora.io/images/video-sdk/icon_android_gradle_sync.png) **Sync Project with Gradle Files** to resolve project dependencies and update the configuration.

1. After synchronization is successful, click ![](https://docs-md.agora.io/images/video-sdk/icon_android_run.png) **Run app**. Android Studio starts compilation. After a few moments, the app is installed on your Android device.

5. Launch the App, grant recording and camera permissions. If you set the user role to host, you will see yourself in the local view.

6. On a second Android device, repeat the previous steps to install and launch the app. Alternatively, use the [Web demo](https://webdemo.agora.io/basicVideoCall/index.html) to join the same channel and test the following use-cases:

    * If users on both devices join the channel as hosts, they can see and hear each other.
    * If one user joins as host and the other as audience, the host can see themselves in the local video window; the audience can see the host in the remote video window and hear the host.


## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

- If a firewall is deployed in your network environment, refer to [Connect with Cloud Proxy](https://docs-md.agora.io/en/video-calling/advanced-features/cloud-proxy.md) to use Agora services normally.

### Next steps

After implementing the quickstart sample, read the following documents to learn more:

* To ensure communication security in a test or production environment, best practice is to obtain and use a token from an authentication server. For details, see [Secure authentication with tokens](https://docs-md.agora.io/en/video-calling/get-started/token-authentication/authentication-workflow.md).

### Sample project

Agora provides open source sample projects on [GitHub](https://github.com/AgoraIO/API-Examples) for your reference. Download or view the [JoinChannelVideo](https://github.com/AgoraIO-Community/Agora-RTC-QuickStart/tree/main/Android/Agora-RTC-QuickStart-Android) project for a more detailed example.

### API reference

- [`RtcEngineConfig`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_rtcengineconfig.html)

- [`create`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_create)

- [`ChannelMediaOptions`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_channelmediaoptions.html)

- [`joinChannel`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_joinchannel2)

- [`enableVideo`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_enablevideo)

- [`startPreview`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_startpreview)

- [`leaveChannel`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_leavechannel)

- [`IRtcEngineEventHandler`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#class_irtcengineeventhandler)

### Frequently asked questions

* [How can I fix black screen issues?](https://docs-md.agora.io/en/help/quality-issues/video_blank.md)
* [Why can't I turn on the camera?](https://docs-md.agora.io/en/help/quality-issues/video_camera.md)

* [How can I listen for audience joining or leaving a channel?](https://docs-md.agora.io/en/help/integration-issues/audience_event.md)
* [How can I solve channel-related issues?](https://docs-md.agora.io/en/help/integration-issues/channel.md)
* [How can I set the log file?](https://docs-md.agora.io/en/help/integration-issues/log.md)
* [Why do apps on some Android versions fail to capture audio and video after screen locking or switching to the background?](https://docs-md.agora.io/en/help/quality-issues/android_background.md)

### See also

* [Error codes](https://docs-md.agora.io/en/video-calling/troubleshooting/error-codes.md)

* [Connection status management](https://docs-md.agora.io/en/video-calling/enhance-call-quality/connection-status-management.md)