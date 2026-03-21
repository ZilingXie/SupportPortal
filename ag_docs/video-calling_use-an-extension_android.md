---
title: Integrate an extension
description: Integrate extensions from Extensions Marketplace directly into your app.
sidebar_position: 14
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/use-an-extension?platform=android
exported_on: '2026-01-20T05:57:08.622870Z'
exported_file: use-an-extension_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/use-an-extension?platform=android)

# Integrate an extension

Extensions are add-ons designed to rapidly extend the functionality of your app. [Extensions Marketplace](https://www.agora.io/en/agora-extensions-marketplace/) is home to extensions that make your app more fun. Extensions provide features such as Audio effects and voice changing, Face filters and background removal, and Live transcription and captioning.

In the Agora Extensions Marketplace:
- Vendors create and publish extensions to provide functionality such as audio and video processing.
- App developers use extensions to quickly implement fun and interactive functionality.

This page shows you how to integrate an extension from Agora Extensions Marketplace into your app. There can be specific guidance for each extension.

## Understand the tech

An extension accesses voice and video data when it is captured from the user's local device, modifies it, then plays the updated data to local and remote video channels.

**Extension call workflow**

![img](https://docs-md.agora.io/images/video-sdk/extension-callflow.svg)

A typical transmission pipeline consists of a chain of procedures, including capture, pre-processing, encoding, transmitting, decoding, post-processing, and play. Audio or video extensions are inserted into either the pre-processing or post-processing procedure, in order to modify the voice or video data in the transmission pipeline.

## Prerequisites

To test the code used in this page you need to have:
* An Agora [account](https://docs-md.agora.io/en/video-calling/get-started/manage-agora-account.md) and [project](https://docs-md.agora.io/en/video-calling/get-started/manage-agora-account.md).
* A computer with Internet access.
    Ensure that no firewall is blocking your network communication.

* Implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md).

## Project setup

In order to integrate an extension into your project:

1. **Activate an extension**

    1. Log in to [Agora Console](https://console.agora.io/v2).
    2. In the left navigation panel, click **Extension Marketplace**, then click the extension you want to activate.

        You are now on the extension detail page.

    3. Select a pricing plan and click **Buy and Activate**.
        - If you have already created an Agora project:

            The **Projects** section appears and lists all of your projects.
        - If you have not created an Agora project:

            [Create a new project](https://docs-md.agora.io/en/video-calling/get-started/manage-agora-account.md), the project appears in the **Projects** section.

    4. Under **Projects** on the extension detail page, find the project in which you want to use the extension, then turn on the switch in the **Action** column.

1. **Get the apiKey and apiSecret for the extension**

    If required for the extension, to get the extension apiKey and apiSecret, in the **Projects** extension detail page, click **View** in the **Secret** column.

3. **Download the extension**

    In the extension detail page, click **Download**, then unzip the extension in a local directory.

1. **Install the extension in your project**

    - Android Archive file (`.aar`)

        1.  Save the extension `.aar` file to `/app/libs` in your project.

        2.  In `/Gradle Scripts/build.gradle(Module: <ProjectName> app)`, add the following line under `dependencies`:

            ```java
            implementation fileTree(include: ['*.jar', '*.aar'], dir: 'libs')
            ```

    - Shared Library (`.so`)

        Save the `.so` file to the following paths in your project:

        1.  `/app/src/main/jniLibs/arm64-v8a`

        2.  `/app/src/main/jniLibs/armeabi-v7a`

You are now ready to integrate the extension in your app.

## Integrate the extension into your project


The watermark extension adds a watermark on video streamed to your local client. This section shows you how to implement the watermark extension
in your Agora project:

1.  **Import the necessary classes**

    1.  Download the [watermark extension](https://web-cdn.agora.io/docs-files/1630400262363) and follow the steps for `.aar` files in [setup](#project-setup).

    2.  In `app/src/main/java/com.example.<projectname>/MainActivity`:

        1.  Add the following lines to import the Android classes used by the extension:

        **Java**
        ```java
        import org.json.JSONException;
                import org.json.JSONObject;
        ```

        **Kotlin**
        ```kotlin
        import org.json.JSONException
                import org.json.JSONObject
        ```


        2.  Add the following lines to import the Agora classes used by the extension:

        **Java**
        ```java
        // ExtensionManager is used to pass in basic info about the extension
                import io.agora.extension.ExtensionManager;
                import io.agora.rtc2.IMediaExtensionObserver;
        ```

        **Kotlin**
        ```kotlin
        // ExtensionManager is used to pass in basic info about the extension
                import io.agora.extension.ExtensionManager
                import io.agora.rtc2.IMediaExtensionObserver
        ```


1. **Add the extension and register the event handler**

    In `setupVideoSDKEngine`, add the following code before `agoraEngine = RtcEngine.create(config);`:

    **Java**
    ```java
    config.addExtension(ExtensionManager.EXTENSION_NAME);
       // Register IMediaExtensionObserver to receive events from the extension.
       config.mExtensionObserver = new IMediaExtensionObserver() {
            @Override
            public void onEvent(String vendor, String extension, String key, String value) {
                // Add callback handling logics for extension events.
                showMessage("Extension: " + extension + "
     Key: " + key + " 
    Value:" + value);
            }
    
            @Override
            public void onStarted(String provider, String extension) {
                showMessage("Extension started");
            }
    
            @Override
            public void onStopped(String provider, String extension) {
                showMessage("Extension stopped");
            }
    
            @Override
            public void onError(String provider, String extension, int error, String message) {
                showMessage(message);
            }
       };
    ```

    **Kotlin**
    ```kotlin
    config.addExtension(ExtensionManager.EXTENSION_NAME)
    
       // Register IMediaExtensionObserver to receive events from the extension.
       config.mExtensionObserver = object : IMediaExtensionObserver {
            override fun onEvent(vendor: String, extension: String, key: String, value: String) {
                // Add callback handling logics for extension events.
                showMessage("Extension: $extension
     Key: $key 
    Value: $value")
            }
    
            override fun onStarted(provider: String, extension: String) {
                showMessage("Extension started")
            }
    
            override fun onStopped(provider: String, extension: String) {
                showMessage("Extension stopped")
            }
    
            override fun onError(provider: String, extension: String, error: Int, message: String) {
                showMessage(message)
            }
       }
    ```


1. **Enable the extension**

    Call `enableExtension` to enable the extension. To enable multiple extensions, call `enableExtension` as many times. The sequence of enabling multiple extensions determines the order of these extensions in the transmission pipeline. For example, if you enable extension A before extension B, extension A processes data from the SDK before extension B.

    In `setupVideoSDKEngine`, add the following code before `agoraEngine = RtcEngine.create(config);`:

    **Java**
    ```java
    agoraEngine.enableExtension(ExtensionManager.EXTENSION_VENDOR_NAME, ExtensionManager.EXTENSION_VIDEO_FILTER_NAME, true);
    ```

    **Kotlin**
    ```kotlin
    agoraEngine.enableExtension(
            ExtensionManager.EXTENSION_VENDOR_NAME,
            ExtensionManager.EXTENSION_VIDEO_FILTER_NAME,
            true
       )
    ```


1. **Set extension properties**

    In the `joinChannel(View view)` method, add the following code after `agoraEngine.joinChannel`: 

    **Java**
    ```java
    JSONObject o = new JSONObject();
        try {
            // Pass in the key-value pairs defined by the extension provider to configure the feature you want to use.
            o.put("plugin.watermark.wmStr", "Agora");
            o.put("plugin.watermark.wmEffectEnabled", true);
    
            // Call setExtensionProperty to use the watermark feature.
            agoraEngine.setExtensionProperty(ExtensionManager.EXTENSION_VENDOR_NAME,
                    ExtensionManager.EXTENSION_VIDEO_FILTER_NAME, "key", o.toString());
        } catch (JSONException e) {
            e.printStackTrace();
        }
    ```

    **Kotlin**
    ```kotlin
    val o = JSONObject()
       try {
            // Pass in the key-value pairs defined by the extension provider to configure the feature you want to use.
            o.put("plugin.watermark.wmStr", "Agora")
            o.put("plugin.watermark.wmEffectEnabled", true)
    
            // Call setExtensionProperty to use the watermark feature.
            agoraEngine.setExtensionProperty(
                ExtensionManager.EXTENSION_VENDOR_NAME,
                ExtensionManager.EXTENSION_VIDEO_FILTER_NAME,
                "key",
                o.toString()
            )
        } catch (e: JSONException) {
            e.printStackTrace()
        }
    ```


## Test your implementation

To ensure that you have integrated the extension in your app:

1.  Connect the Android device to the computer.

2.  Click `Run app` on your Android Studio. A moment later you will see the project installed on your device.

3.  When the app launches, you can see yourself and the watermark `Agora` on the local view.

## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### API reference

* [`RtcEngineConfig.addExtension`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/rtc_api_data_type.html#api_irtcengine_addextension)

* [`enableExtension`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_enableextension)

* [`getExtensionProperty`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_getextensionproperty)

* [`setExtensionProperty`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setextensionproperty)

* [`IMediaExtensionObserver`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaextensionobserver.html)