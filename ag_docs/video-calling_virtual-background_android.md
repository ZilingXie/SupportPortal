---
title: Virtual Background
description: Blur the background or replace it with a solid color or an image.
sidebar_position: 9
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/virtual-background?platform=android
exported_on: '2026-01-20T05:57:12.545511Z'
exported_file: virtual-background_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/virtual-background?platform=android)

# Virtual Background

Virtual Background enables users to blur their background, or replace it with a solid color or an image. This feature is applicable to use-cases such as online conferences, online classes, and live streaming. It helps protect personal privacy and reduces audience distraction.

## Understand the tech

Virtual Background offers the following options:

|  Feature  |  Example |
| ---- | ---- |
| Blurred background and image background   |   ![](https://docs-md.agora.io/images/extensions-marketplace/blurred-background.jpg)   |
| Video/Animated background   |   <video src={videoURL} poster="https://web-cdn.agora.io/docs-files/1654571689670"  controls width="100%" height="auto">Your browser does not support the `video`  element.</video>   |
| Portrait-in-picture   | ![portrait-in-picture](https://docs-md.agora.io/images/extensions-marketplace/portrait-in-picture.jpg) Allows the presenter to use slides as the virtual background while superimposing their video. The effect is similar to a weather news cast on television, preventing interruptions during a layout toggle.   |

Want to test Virtual Background? Try the <a href="https://webdemo.agora.io/virtualBackground/index.html">online demo</a>.

## Prerequisites
Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implement virtual background

This section shows you how to add a virtual background to the local video.


### Check device compatibility

To avoid performance degradation or unavailable features when enabling Virtual Background on low-end devices, check whether the device supports the feature.

**Java**
```java
boolean isFeatureAvailable() {
    return agoraEngine.isFeatureAvailableOnDevice(
        Constants.FEATURE_VIDEO_VIRTUAL_BACKGROUND
    );
}
```

**Kotlin**
```kotlin
fun isFeatureAvailable(): Boolean {
    return agoraEngine.isFeatureAvailableOnDevice(
        Constants.FEATURE_VIDEO_VIRTUAL_BACKGROUND
    )
}
```


### Set a blurred background

To blur the video background, use the following code:

**Java**
```java
void setBlurBackground() {
    VirtualBackgroundSource virtualBackgroundSource = new VirtualBackgroundSource();
    virtualBackgroundSource.setBackgroundSourceType(VirtualBackgroundSource.BACKGROUND_BLUR);
    virtualBackgroundSource.setBlurDegree(VirtualBackgroundSource.BLUR_DEGREE_MEDIUM);

    // Set processing properties for background
    SegmentationProperty segmentationProperty = new SegmentationProperty();
    segmentationProperty.setModelType(SegmentationProperty.SEG_MODEL_AI);
    // Use SEG_MODEL_GREEN if you have a green background
    segmentationProperty.setGreenCapacity(0.5f); // Accuracy for identifying green colors (range 0-1)

    // Enable or disable virtual background
    agoraEngine.enableVirtualBackground(true, virtualBackgroundSource, segmentationProperty);
}
```

**Kotlin**
```kotlin
fun setBlurBackground() {
    val virtualBackgroundSource = VirtualBackgroundSource()
    virtualBackgroundSource.setBackgroundSourceType(VirtualBackgroundSource.BACKGROUND_BLUR)
    virtualBackgroundSource.setBlurDegree(VirtualBackgroundSource.BLUR_DEGREE_MEDIUM)

    // Set processing properties for background
    val segmentationProperty = SegmentationProperty()
    segmentationProperty.setModelType(SegmentationProperty.SEG_MODEL_AI)
    // Use SEG_MODEL_GREEN if you have a green background
    segmentationProperty.setGreenCapacity(0.5f) // Accuracy for identifying green colors (range 0-1)

    // Enable or disable virtual background
    agoraEngine.enableVirtualBackground(true, virtualBackgroundSource, segmentationProperty)
}
```


### Set a color background

To apply a solid color as the virtual background, use a hexadecimal color code. For example, `0x0000FF` for blue:

**Java**
```java
void setSolidBackground() {
    VirtualBackgroundSource virtualBackgroundSource = new VirtualBackgroundSource();
    virtualBackgroundSource.setBackgroundSourceType(VirtualBackgroundSource.BACKGROUND_COLOR);
    virtualBackgroundSource.setColor(0x0000FF);

    // Set processing properties for background
    SegmentationProperty segmentationProperty = new SegmentationProperty();
    segmentationProperty.setModelType(SegmentationProperty.SEG_MODEL_AI);
    // Use SEG_MODEL_GREEN if you have a green background
    segmentationProperty.setGreenCapacity(0.5f); // Accuracy for identifying green colors (range 0-1)

    // Enable or disable virtual background
    agoraEngine.enableVirtualBackground(true, virtualBackgroundSource, segmentationProperty);
}
```

**Kotlin**
```kotlin
fun setSolidBackground() {
    val virtualBackgroundSource = VirtualBackgroundSource()
    virtualBackgroundSource.setBackgroundSourceType(VirtualBackgroundSource.BACKGROUND_COLOR)
    virtualBackgroundSource.setColor(0x0000FF)

    // Set processing properties for background
    val segmentationProperty = SegmentationProperty()
    segmentationProperty.setModelType(SegmentationProperty.SEG_MODEL_AI)
    // Use SEG_MODEL_GREEN if you have a green background
    segmentationProperty.setGreenCapacity(0.5f) // Accuracy for identifying green colors (range 0-1)

    // Enable or disable virtual background
    agoraEngine.enableVirtualBackground(true, virtualBackgroundSource, segmentationProperty)
}
```


### Set an image background

To set a custom image as the virtual background, specify the absolute path to the image file.

**Java**
```java
void setImageBackground() {
    VirtualBackgroundSource virtualBackgroundSource = new VirtualBackgroundSource();
    virtualBackgroundSource.setBackgroundSourceType(VirtualBackgroundSource.BACKGROUND_IMG);
    virtualBackgroundSource.setSource("<absolute path to an image file>");

    // Set processing properties for background
    SegmentationProperty segmentationProperty = new SegmentationProperty();
    segmentationProperty.setModelType(SegmentationProperty.SEG_MODEL_AI);
    // Use SEG_MODEL_GREEN if you have a green background
    segmentationProperty.setGreenCapacity(0.5f); // Accuracy for identifying green colors (range 0-1)

    // Enable or disable virtual background
    agoraEngine.enableVirtualBackground(true, virtualBackgroundSource, segmentationProperty);
}
```

**Kotlin**
```kotlin
fun setImageBackground() {
    val virtualBackgroundSource = VirtualBackgroundSource()
    virtualBackgroundSource.setBackgroundSourceType(VirtualBackgroundSource.BACKGROUND_IMG)
    virtualBackgroundSource.setSource("<absolute path to an image file>")

    // Set processing properties for background
    val segmentationProperty = SegmentationProperty()
    segmentationProperty.setModelType(SegmentationProperty.SEG_MODEL_AI)
    // Use SEG_MODEL_GREEN if you have a green background
    segmentationProperty.setGreenCapacity(0.5f) // Accuracy for identifying green colors (range 0-1)

    // Enable or disable virtual background
    agoraEngine.enableVirtualBackground(true, virtualBackgroundSource, segmentationProperty)
}
```


### Reset the background

To disable the virtual background and revert to the original video state, use the following code:

**Java**
```java
void removeBackground() {
    // Disable virtual background
    agoraEngine.enableVirtualBackground(
        false,
        new VirtualBackgroundSource(),
        new SegmentationProperty()
    );
}
```

**Kotlin**
```kotlin
fun removeBackground() {
    // Disable virtual background
    agoraEngine.enableVirtualBackground(
        false,
        VirtualBackgroundSource(),
        SegmentationProperty()
    )
}
```

## Reference

This section contains content that completes the information in this page, or points you to documentation that explains other aspects to this product. 

### API reference

- [`isFeatureAvailableOnDevice`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_isfeatureavailableondevice)

- [`enableVirtualBackground`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_enablevirtualbackground)

- [`VirtualBackgroundSource`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_virtualbackgroundsource.html)

- [`SegmentationProperty`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/rtc_api_data_type.html#class_segmentationproperty)