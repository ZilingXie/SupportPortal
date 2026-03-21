---
title: Compile and run a sample project
description: Compile and run a project using Agora SDK
sidebar_position: 6
platform: android
exported_from: https://docs.agora.io/en/video-calling/get-started/compile-run-sample-project?platform=android
exported_on: '2026-01-20T05:58:04.831511Z'
exported_file: compile-run-sample-project_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/get-started/compile-run-sample-project?platform=android)

# Compile and run a sample project

Agora provides [Open-source sample projects on GitHub](https://github.com/AgoraIO/API-Examples/tree/main) to demonstrate the implementation of basic and advanced Video SDK features.

This page shows how to compile, configure, and run the Video SDK Android sample project.

## Prerequisites

- [Android Studio](https://developer.android.com/studio) 4.2 or higher.
- Android SDK API Level 21 or higher.
- Two mobile devices running Android 5.0 or higher.


- A camera and a microphone

- A valid Agora account and project. Please refer to [Agora account management](https://docs-md.agora.io/en/video-calling/get-started/manage-agora-account.md) for details.

- Installed [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)

## Project setup

### Get the sample project

Run the following command to clone the repository locally:

```bash
git clone git@github.com:AgoraIO/API-Examples.git
```

This repository contains sample projects for all native platforms of Agora Video SDK. The API usage examples for Android are located under `/Android`.

| Path |  	Description |
|:------|:---------------|
| `/Android/APIExample` |	API usage examples of Agora Video SDK. |
| `/Android/APIExample-Audio` | API usage examples of Agora Voice SDK. |

### Configure the sample project

> ⚠️ **information**
> This page refers to the video sample project. If you want to run the audio sample project, please refer to the following steps and operate in the audio folder.

1. Integrate the SDK and install dependencies

    The `/APIExample/app/build.gradle` file contains the project dependencies. When you open the project in Android Studio, it automatically downloads the dependencies and integrates the SDK.

1. Set App ID and app certificate

    Open the `/APIExample/app/src/main/res/values/string-config.xml` file and fill in the App ID and app certificate you obtained from the Agora Console.

    ```xml
    <?xml version="1.0" encoding="utf-8"?>
    <resources>
        <!--
        Agora App ID
        -->
        <string name="agora_app_id" translatable="false">Your App ID</string>

        <!--
        Agora app certificate
        Note: Leave this field empty if the project allows joining by App ID only.
        -->
        <string name="agora_app_certificate" translatable="false">Your app certificate</string>

    </resources>
    ```

## Run the sample project


1. Open the `/API-Examples/Android/APIExample` folder in Android Studio.

1. Turn on developer options on your Android device, enable USB debugging, and connect the device to the development machine through a USB cable. Your Android device appears in the Android device options.

    ![](https://docs-md.agora.io/images/video-sdk/run-compile-android-step-2.png)

1. In Android Studio, click **Sync Project with Gradle Files** for Gradle synchronization.

1. After synchronization is complete, click **Run 'app'** to start compilation.

    The app is installed as **Agora API Example** on your device.

1. Open the app and choose the example you want to run. For example, to test **Live Interactive Video Streaming**, select the option from the menu, enter the channel name, and click **Join**.

    ![](https://docs-md.agora.io/images/video-sdk/run-compile-android-step-6.jpg)

1. To test various audio and video interaction use-cases, connect to the [Agora web demo](https://webdemo.agora.io/basicVideoCall/index.html), or install and run the sample project on a second device. Make sure you use the same app ID on both devices. When you join the same channel from two devices, you can see and hear each other.

    ![](https://docs-md.agora.io/images/video-sdk/run-compile-android-step-7.png)
