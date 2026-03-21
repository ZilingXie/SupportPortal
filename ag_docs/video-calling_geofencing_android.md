---
title: Restrict media zones
description: Control and customize video SDK data routing in your app
sidebar_position: 20
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/geofencing?platform=android
exported_on: '2026-01-20T05:56:33.693359Z'
exported_file: geofencing_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/geofencing?platform=android)

# Restrict media zones

When a user joins a channel, Video SDK automatically connects them to the Agora SDRTN® media zone that is geographically closest to the user. However, to meet the laws and regulations of the user's country, you may need to specify, or filter out connections to a specific geographical zone. Agora enables you to control and customize data routing in your app by specifying the Agora SDRTN® media zone users connect to.

> ℹ️ **information**
> This is an advanced feature suitable only for use-cases with access security restrictions.

## Understand the tech

After you turn on the restricted media zones feature, the SDK only accesses the Agora server in the specified zone(s), irrespective of the geographical location of the user. As an example, the following table shows the outcome if you specify North America as the access zone, and users connect to Agora SDRTN® from North America and China respectively:

<table>
  <thead>
    <tr>
      <th>Designated access zone</th>
      <th>User's location</th>
      <th>Zone actually accessed by the SDK</th>
      <th>User experience</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td rowspan="2">North America</td>
      <td>North America</td>
      <td rowspan="2">North America</td>
      <td>Normal</td>
    </tr>
    <tr>
      <td>China</td>
      <td>Quality may be affected</td>
    </tr>
  </tbody>
</table>

> ℹ️ **information**
> * If a server in the specified zone is not available, the SDK reports an error.
> * Due to cross-regional public internet between the designated zone and the geographical location where the app user is located, the audio and video experience may be affected.

The following figure shows the workflow you implement to restrict access to media zones:

**Restrict media zones**

![Restrict media zones](https://docs-md.agora.io/images/video-sdk/restrict-media-zones.svg)

## Prerequisites

Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implement restricted media zones
This section shows you how to restrict access to media zones in your app.


You specify the access zones by setting the `RtcEngineConfig.mAreaCode` parameter when calling the `create` method to create an `RtcEngine` instance. The available zones are:

* `AREA_CODE_GLOB`: Global (Default)
* `AREA_CODE_CN`  : Mainland China 
* `AREA_CODE_NA`  : North America
* `AREA_CODE_EU`  : Europe
* `AREA_CODE_AS`  : Asia excluding Mainland China
* `AREA_CODE_JP`  : Japan
* `AREA_CODE_IN`  : India

> ℹ️ **information**
> Media zones support bitwise operations.

### Include a media zone

To limit access to servers in only one zone, such as North America, set `config.mAreaCode = AREA_CODE_NA;` before creating the `RtcEngine` instance:

**Java**
```java
// Initialize the App and join the channel
private void initializeAndJoinChannel() {
    try {
        RtcEngineConfig config = new RtcEngineConfig();
        config.mAppId = appId;
        config.mContext = mContext;
        config.mEventHandler = mEngineEventHandler.mRtcEventHandler;
        // Restrict access to servers in North America only
        config.mAreaCode = AREA_CODE_NA;
        mRtcEngine = RtcEngine.create(config);
    } catch (Exception e) {
            throw new RuntimeException("Check the error.");
    }
}
```

**Kotlin**
```kotlin
// Initialize the App and join the channel
private fun initializeAndJoinChannel() {
    try {
        val config = RtcEngineConfig()
        config.mAppId = appId
        config.mContext = mContext
        config.mEventHandler = mEngineEventHandler.mRtcEventHandler
        // Restrict access to servers in North America only
        config.mAreaCode = AREA_CODE_NA
        mRtcEngine = RtcEngine.create(config)
    } catch (e: Exception) {
            throw RuntimeException("Check the error.")
    }
}
```


### Exclude a media zone

To exclude servers in a zone, such as Mainland China, set `config.mAreaCode = AREA_CODE_GLOB ^ AREA_CODE_CN;` before creating the `RtcEngine` instance:

**Java**
```java
// Initialize the App and join the channel
private void initializeAndJoinChannel() {
    try {
        RtcEngineConfig config = new RtcEngineConfig();
        config.mAppId = appId;
        config.mContext = mContext;
        config.mEventHandler = mEngineEventHandler.mRtcEventHandler;
        // Exclude Mainland China from access zones
        config.mAreaCode = AREA_CODE_GLOB ^ AREA_CODE_CN;
        mRtcEngine = RtcEngine.create(config);
    } catch (Exception e) {
            throw new RuntimeException("Check the error.");
    }
}
```

**Kotlin**
```kotlin
// Initialize the App and join the channel
private fun initializeAndJoinChannel() {
    try {
        val config = RtcEngineConfig()
        config.mAppId = appId
        config.mContext = mContext
        config.mEventHandler = mEngineEventHandler.mRtcEventHandler
        // Exclude Mainland China from access zones
        config.mAreaCode = AREA_CODE_GLOB xor AREA_CODE_CN
        mRtcEngine = RtcEngine.create(config)
    } catch (e: Exception) {
            throw RuntimeException("Check the error.")
    }
}
```


## Reference
This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### API reference

- [`create` [2/2]](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_initialize)