---
title: Connect with Cloud Proxy
description: Implement Agora Cloud Proxy feature for reliable audio and video connectivity.
sidebar_position: 11
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/cloud-proxy?platform=android
exported_on: '2026-01-20T05:56:22.966450Z'
exported_file: cloud-proxy_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/cloud-proxy?platform=android)

# Connect with Cloud Proxy

Large enterprises, hospitals, universities, banks, and other institutions commonly deploy firewalls to meet their stringent security requirements. To ensure uninterrupted access to its services for enterprise users behind firewalls, Agora offers firewall whitelist and Cloud proxy services.

Admins enable users to use Video SDK in network-restricted environments by adding specific IP addresses and ports to the firewall whitelist. Users make API calls to configure the Cloud proxy service.

## Understand the tech

Cloud proxy works as follows:

1. Video SDK initiates a request to Cloud proxy.
1. Cloud proxy returns the corresponding proxy information.
1. Agora SDK sends data to Cloud proxy. Cloud proxy receives the data and transmits it to Agora SDRTN®.
1. SDRTN® returns data to Cloud proxy. Cloud proxy receives the data and sends it to the SDK.

**Cloud proxy workflow**

![](https://docs-md.agora.io/images/video-sdk/cloud-proxy-tech.svg)

## Prerequisites

Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implement Cloud proxy

Take the following steps to implement the use of Cloud proxy in your app:

1. Contact [Agora support](https://docs-md.agora.io/en/mailto:support@agora.io.md) and provide the following information to request Cloud proxy service:
    - App ID
    - Cloud proxy service usage area
    - Concurrency scale
    - Network operator and other relevant information

2. After receiving the request, Agora provides the IP addresses and ports used for Cloud proxy.

3. Add the IP addresses and ports provided by Agora to your firewall whitelist.

4. Call `setCloudProxy` and set `proxyType` to `TRANSPORT_TYPE_UDP_PROXY` or `TRANSPORT_TYPE_TCP_PROXY` to enable Cloud proxy.

5. Test if you can initiate audio and video calls or live broadcasts normally.

6. To stop using Cloud proxy, call `setCloudProxy` and set `proxyType` to `TRANSPORT_TYPE_NONE_PROXY`.

7. To update the current `proxyType`, call `setCloudProxy(TRANSPORT_TYPE_NONE_PROXY)` first, then call `setCloudProxy` again with the new `proxyType`.

Call `setCloudProxy` outside the channel. Its settings are valid within the `RtcEngine` life cycle.


## Reference
This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Pricing

Agora charges for use of Cloud Proxy as follows.

### Duration-based pricing

Duration-based pricing is based on the total number of minutes used per month across all users.

| Duration | Price per 1000 minutes/month |
|:-----------------------|-----------------------------:|
| UDP Audio Duration     | $0.99 |
| UDP video HD Duration  | $3.99​ |
| UDP video Full HD Duration | $8.99 |
| UDP video 2K Duration  | $15.99​ |
| UDP video 2K+ Duration | $35.99 |
| TCP Audio Duration     | ​$0.99 |
| TCP video HD Duration  | $3.99​ |
| TCP video Full HD Duration | $8.99 |
| TCP video 2K Duration  | $15.99​ |
| TCP video 2K+ Duration | $35.99 |

For volume pricing discount, contact [Agora support](https://docs-md.agora.io/en/mailto:support@agora.io.md).

#### PCU-based pricing

PCU is the maximum number of simultaneous users connected to the Cloud Proxy service at any point during the billing cycle. For PCU-based usage, Agora charges $500.00 for each VID. 

For volume pricing discount, contact [Agora support](https://docs-md.agora.io/en/mailto:support@agora.io.md).

### IP addresses for Cloud Proxy

To use Agora Cloud Proxy, your end users must first configure their firewalls to trust the following [IP address and port ranges](https://docs-md.agora.io/en/video-calling/reference/cloud-proxy-allowed-ips.md).

> ⚠️ **Caution**
> - If a user is in an intranet firewall environment and uses `TRANSPORT_TYPE_UDP_PROXY`, the services for Media Push and Co-hosting across channels are not available.

### API reference

- [`setCloudProxy`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setcloudproxy)