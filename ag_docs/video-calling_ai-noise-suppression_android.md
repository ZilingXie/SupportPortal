---
title: AI Noise Suppression
description: Suppress hundreds of types of noise and reduce distortion for human voice
sidebar_position: 15
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/ai-noise-suppression?platform=android
exported_on: '2026-01-20T05:56:09.210915Z'
exported_file: ai-noise-suppression_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/ai-noise-suppression?platform=android)

# AI Noise Suppression


AI Noise Suppression enables you to suppress hundreds of types of noise and reduce distortion in human voices when multiple people speak at the same time. In use-cases such as online meetings, online chat rooms, video consultations with doctors, and online gaming, AI Noise Suppression makes virtual communication as smooth as face-to-face interaction.

<a name="type"></a>
AI Noise Suppression reduces the following types of noise:

- Television
- Kitchen
- Street, such as birds chirping, traffic, and subway sounds
- Machine, such as fans, air conditioners, vacuum cleaners, and copiers
- Office, such as keyboard and mouse clicks
- Household, such as doors opening, creaking chairs, crying babies, and house renovations
- Constant knocking
- Beeps and clapping
- Music

You can choose following noise reduction strategies:
- Default: Reduces noise to a comfortable level without distorting human voice.
- Custom: A more enhanced or customized noise reduction strategy for your business use-case. Contact [support@agora.io](https://docs-md.agora.io/en/mailto:support@agora.io.md) for details.

Want to try out AI Noise Suppression? Use the <a href="https://webdemo.agora.io/aiDenoiser/index.html">online demo</a>.

## Understand the tech

In the pre-processing stage, AI Noise Suppression uses deep learning noise reduction algorithms to modify <audio src=""></audio> data in the extensions pipeline.

**AI noise suppression**

![](https://docs-md.agora.io/images/extensions-marketplace/ai-noise-suppression.svg)

## Prerequisites

Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Enable AI Noise Suppression 

This section shows you how to integrate AI Noise Suppression into your app. 


Call `setAINSMode` to enable the AI noise suppression feature, and select a noise suppression mode:

**Java**
```java
// Noise reductions modes
// 0 -> Balance mode 
// 1 -> Aggressive mode
// 2 -> Aggressive mode with low latency
int mode =  1;
// Set the mode for Audio AI Noise Suppression
int result = rtcEngine.setAINSMode(true, mode);
```

**Kotlin**
```kotlin
// Noise reductions modes
// 0 -> Balance mode 
// 1 -> Aggressive mode
// 2 -> Aggressive mode with low latency
val mode = 1
// Set the mode for Audio AI Noise Suppression
val result = rtcEngine.setAINSMode(true, mode)
```


## Reference

This section completes the information on this page, or points you to documentation that explains other aspects about this product.

### API reference

  * <Link to = "{{global.API_REF_ANDROID_ROOT}}/class_irtcengine.html#api_irtcengine_setainsmode">`setAINSMode`</Link>

### Considerations

Currently,  AI Noise Suppression has the following limitations:

- If the sample rate of the input signal is not 16 kHz,  AI Noise Suppression:
    1. Down-samples the signal to 16 kHz.
    1. Removes noise.
    1. Resamples the output signal to the original sample rate.

    This means that audio data above 8 kHz is removed in the output signal.

- In some use-cases, AI Noise Suppression may cause audio quality to decrease by a certain degree.
- When multiple people speak at the same time, the audio quality of lowest human voices could be decreased by a certain degree.