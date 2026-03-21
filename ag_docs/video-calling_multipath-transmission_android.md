---
title: Multipath network transmission
description: Play local or online media files locally or to remote users in an Agora
  channel.
sidebar_position: 8.6
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/multipath-transmission?platform=android
exported_on: '2026-01-20T05:56:44.559680Z'
exported_file: multipath-transmission_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/multipath-transmission?platform=android)

# Multipath network transmission

A growing number of electronic devices support simultaneous network access through multiple connections, such as Wi-Fi and cellular, or Wi-Fi and dual-SIM cellular. Each connection is independent of the actual network transmission path and does not share bandwidth bottlenecks.

This page explains how to implement multipath network transmission in your project.

## Understand the tech

Starting with v4.6.0, the Video SDK adds a new network transmission feature called Multipath. This feature is designed for devices that support multiple network interfaces, including 5G, Wi-Fi, and LAN. Multipath helps reduce or even eliminate poor user experiences caused by weak network conditions. In lab tests, under weak network conditions with frequent bandwidth jitter, enabling Multipath reduces lag by more than 50% while maintaining image quality and latency.

Multipath improves real-time audio and video experiences that require stable transmission in poor network conditions, including video conferencing, online education, IoT parallel operations, and remote production and broadcasting.

## Prerequisites

Before you begin, make sure you have:

- A mobile device running Android 7.0 (API level 24) or later, with two or more active network connections.

- Basic audio and video interaction functions implemented in your project. See [Video Calling Quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md).

## Implementation

To implement Multipath network transmission in your project:

### Add network permissions

Multipath requires permission to access and manage the device network state. You must add the following permissions to the Android project’s `/app/src/main/AndroidManifest.xml` file:

```xml
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>
<uses-permission android:name="android.permission.CHANGE_NETWORK_STATE"/>
```

### Multipath APIs

The Video SDK supports enabling and configuring multipath transmission capabilities through the following parameters in `ChannelMediaOptions`:

```java
public class ChannelMediaOptions {
  // ...
  public Boolean enableMultipath;
  public Integer uplinkMultipathMode;
  public Integer downlinkMultipathMode;
  public Integer preferMultipathType;
}
```

Use `enableMultipath` to enable multipath transmission. Once enabled, the SDK supports two transmission modes:

- **Dynamic mode** (default): Dynamically selects the optimal transmission path based on network conditions. This mode is suitable for traffic-sensitive scenarios with high user experience requirements, such as conferences and education. In this mode, you can use `preferMultipathType` to specify a preferred network path, such as Wi-Fi or mobile network.

- **Full redundancy mode**: Sends data simultaneously across all available network paths. This mode is suitable for scenarios where traffic use is less sensitive but a highly reliable user experience is required, such as outdoor production, broadcasting, and parallel operation. This mode incurs additional fees. [Contact technical support](https://docs-md.agora.io/en/mailto:support@agora.io.md) to enable it.

You can configure the transmission mode separately for uplink (`uplinkMultipathMode`) and downlink (`downlinkMultipathMode`).

When multipath is enabled, the SDK reports transmission statistics for each path in real time through the `onMultipathStats` callback. These statistics include traffic consumption for each network type and real-time performance metrics for each path, making it easier to monitor and optimize network performance.

```java
public static class MultipathStats {
  public int lanTxBytes;
  public int lanRxBytes;
  public int wifiTxBytes;
  public int wifiRxBytes;
  public int mobileTxBytes;
  public int mobileRxBytes;
  public int activePathNum;
  public PathStats[] pathStats;
}
```

### Sample code

The following sample code shows how to enable and configure multipath network transmission:

```java
private String multipathModeStr = "";
private int activePathNum = 0;

// Enable multipath network transmission
mediaOptions.enableMultipath = true;

multipathModeStr = spinner_multipath_mode.getSelectedItem().toString();
Constants.MultipathMode multipathMode = Constants.MultipathMode.valueOf(multipathModeStr);

// Set uplink transmission mode
mediaOptions.uplinkMultipathMode = Constants.MultipathMode.getValue(multipathMode);

// Set downlink transmission mode
mediaOptions.downlinkMultipathMode = Constants.MultipathMode.getValue(multipathMode);

// In dynamic transmission mode, use preferMultipathType to specify the preferred path
mediaOptions.preferMultipathType = Constants.MultipathType.MULTIPATH_TYPE_WIFI.getValue();

// Report multipath transmission statistics
@Override
public void onMultipathStats(MultipathStats stats) {
  super.onMultipathStats(stats);
  activePathNum = stats.activePathNum;
}
```

## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### API reference

- [`ChannelMediaOptions`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_channelmediaoptions.html)
- [`onMultipathStats`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onmultipathstats)