---
title: Simulcasting multi-bitrate video streams (Beta)
description: Play local or online media files locally or to remote users in an Agora
  channel.
sidebar_position: 8.5
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/simulcasting?platform=android
exported_on: '2026-01-20T05:57:01.276223Z'
exported_file: simulcasting_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/simulcasting?platform=android)

# Simulcasting multi-bitrate video streams (Beta)

In real-time interactive scenarios such as live broadcasts, conferences, and large-class lectures, adjusting video resolution dynamically improves the user experience across different terminals and network conditions.

Common use cases include:

- **Live streaming**: Push high resolution, and fall back to low resolution under poor network conditions.

- **Video conferencing**: Use high resolution for the main speaker and low resolution for thumbnails.

- **Multi-terminal adaptation**: Deliver high resolution to high-performance devices and low resolution to low-performance devices to optimize bandwidth.

- **Multi-screen adaptation**: Stream different resolutions for large, medium, and small screens.

This guide shows you how to use the Video SDK to implement multi-bitrate video streaming.

## Understand the tech

Starting with v4.6.0, Video SDK introduces simulcasting, which enables you to send up to four simultaneous streams of different configurations (resolutions and frame rates) from the same video source: one source stream and up to three additional configurations. Subscribers can choose different stream levels based on rendering window size or business logic, balancing image quality, performance, bandwidth, and network conditions. If bandwidth or device performance is limited, the sender automatically disables extra streams.

Compared to dual-stream video, simulcasting supports more stream configurations and smoother switching. If you already use dual-stream video, see [Simulcasting vs. Dual-stream video](#simulcasting-vs-dual-stream-video) to choose the right solution for your needs.

> ℹ️ **Additional information about simulcasting**
> - Each client can publish up to four simulcast configurations, including both the large and small streams. If you need more (up to eight streams), [contact technical support](https://docs-md.agora.io/en/mailto:support@agora.io.md).
> - Simulcasting supports publishing video streams at specific tiers based on the subscriber’s settings. This feature is disabled by default. [Contact technical support](https://docs-md.agora.io/en/mailto:support@agora.io.md) to enable.
> - Simulcasting is currently available only for video publishing and subscription on the native platforms. It is not supported on the Web platform. [Contact technical support](https://docs-md.agora.io/en/mailto:support@agora.io.md) if you need it.
> - Cloud recording supports only large or small stream when simulcasting is enabled.

## Prerequisites

Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Publish multi-bitrate video streams

To enable and implement this feature, contact [Agora support](https://docs-md.agora.io/en/mailto:support@agora.io.md).

## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Simulcasting vs. Dual-stream video

Simulcasting and dual-stream video (`setDualStreamMode`) are mutually exclusive. The API you set later overrides the earlier one. Choose the solution that fits your business needs and disable the other.

Compared with dual-stream video, simulcasting offers the following improvements:

- **More subscribed streams**: Simulcasting expands the number of subscribable streams from 2 to 8. On top of the original large (`VideoStreamType.VIDEO_STREAM_HIGH`) and small (`VideoStreamType.VIDEO_STREAM_LOW`) streams, simulcasting adds six additional configurations. These are defined as `VIDEO_STREAM_LAYER_1` to `VIDEO_STREAM_LAYER_6`, corresponding to `SimulcastConfig.StreamLayerIndex.STREAM_LAYER_1` to `SimulcastConfig.StreamLayerIndex.STREAM_LAYER_6`.

- **Smoother switching experience**: Instead of the two-level switching between large and small streams, simulcasting supports multi-level bitrate switching. This provides a smoother experience and more flexibility for different business scenarios.

To better balance simulcasting performance and network consumption, Agora has implemented the following strategies:

- When the sender device is performance-limited, upstream configurations are automatically disabled, and subscribers adapt to the remaining streams.

- When the uplink bandwidth is limited, additional configurations are disabled first. Then, the previous dual-stream rate control strategy is applied to ensure smooth publishing and subscription.

In terms of functionality, the differences between simulcasting and dual-stream video are summarized in the following table. Agora will continue to close the feature gap in future releases.

| Feature | Simulcasting | Dual-stream video |
|---------|:------------:|:-----------------:|
| Supports multi-channel scenarios | ✔ | ✔ |
| Small stream automatically adapts video attributes | ✘ | ✔ |
| Publisher sends video streams based on subscriber’s chosen configuration | ✔ <br/>Contact technical support to enable | ✔ |
| When the sender is performance-limited, video streams other than the source stream can be disabled | ✔ | ✘ |
| When the downlink network is limited, the upstream small stream can be enabled automatically | ✘ | ✔ |

### API reference

- [`setSimulcastConfig`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setsimulcastconfig)
- [`setRemoteVideoStreamType`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setremotevideostreamtype2)

 */}