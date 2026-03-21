---
title: Device management
description: ''
sidebar_position: 14
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/set-audio-route?platform=android
exported_on: '2026-01-20T05:56:59.311565Z'
exported_file: set-audio-route_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/set-audio-route?platform=android)

# Device management

Audio routing refers to selection of the audio output device used for playback by your app. This can include a variety of devices such as earpieces, headphones, speakers, and Bluetooth devices capable of playing audio.

This page shows you how to change audio routing in your app.

## Understand the tech

Audio routing enables you to change the playback device from the default device to a selected device.

#### Default audio routing

The routing employed by the device itself, such as the speakers on a mobile device, is called the default audio routing of that device.

The SDK uses the following default audio routing in different use-cases:

- **Voice call**: Earpiece
- **Video call**: Speaker
- **Live voice call**: Speaker
- **Live video streaming**: Speaker

## Change audio routing

Depending on your role, you can change audio routing in the following ways:

- **Device user**: Add or remove external devices, such as headphones or Bluetooth devices.
- **App developer**:
    - Before joining a channel, call `{props.setAudioRouteAPI}` to change the default audio routing.
    - After joining a channel, call `{props.setEnableSpeakerphoneAPI}` to set the current audio routing.

Regardless of the method used to make changes to audio routing, the priority for the changes to take effect follows these principles:

1. User behavior has the highest priority for audio routing changes.
When a user connects an external device such as a wired headset, or a Bluetooth headset, audio routing automatically switches to the external device. If the user connects multiple external devices successively, audio routing will automatically switch to the last connected device.

1. When no external device is connected, the SDK uses the default audio routing.
Call `{props.setAudioRouteAPI}` to change this default setting. If the current audio route is the device itself, `{props.setAudioRouteAPI}` modifies the current audio route.

1. Without system limitations, calling `{props.setEnableSpeakerphoneAPI}` switches the current audio routing regardless of whether the current audio routing is an external device, a speaker, or an earpiece.
However, this method only works for the current channel, it does not change the default audio routing of the device. If the user leaves the current channel and joins a new channel, the SDK still uses the default audio route.

Any changes to the audio route trigger the `{props.onAudioRouteChangedAPI}` callback. Use this callback to get the current audio route.

### Plug and unplug devices to change audio routing

Audio routing changes when the user connects an audio device. For example, when headphones are plugged in, audio routing automatically switches to headphones. When multiple devices are connected, audio routing switches to the last connected device.

Refer to the following example to see how plugging and unplugging a device changes the audio routing:

1. A user joins the channel. Audio is routed to speakers.

1. The user plugs in headphones. Audio routing changes to headphones.

1. The user connects the mobile device to a Bluetooth audio device without unplugging the headset. Audio routing changes to the Bluetooth audio device.

1. The user disconnects the Bluetooth audio device from the mobile device. Audio routing changes back to headphones.

### Change the default audio routing

Before joining a channel, call the `{props.setAudioRouteAPI}` method to switch the default audio routing to either the handset or the speakerphone. Set the `defaultToSpeaker` parameter of this method to `true` to set the default audio route to speakerphone, and to `false` to set the default audio route to handset. The setting remains valid until you destroy the engine.

Refer to the following example to understand how to change the default audio routing:

1. The user plugs in the headset. Audio is routed to the headset.

1. The user unplugs the headset. Audio routing is changed to the default audio routing of the mobile device, depending on your usage scenario.

1. Call `{props.setAudioRouteAPI}(true)` in the app. Audio routing is changed to speakerphone.

1. User plugs in headphones. Audio routing changes to headphones.

1. Call `{props.setAudioRouteAPI}(true)`. The audio is still routed to the headset because `{props.setAudioRouteAPI}` only works for the audio routing of the device.

1. The user unplugs the headphones. The audio routing is changed to speakerphone.

### Changing the current audio routing

If the operating system allows it, call the `{props.setEnableSpeakerphoneAPI}` method to change the current audio routing to speaker.

Refer to the following example to understand how to change the current audio routing:

- Example 1:
    1. User joins a channel. Audio is routed to the speaker.
    1. User plugs in headphones. Audio routing is changed to headphones.
    1. App calls `{props.setEnableSpeakerphoneAPI}(true)`. Audio is still routed to headphones.

- Example 2:
    1. A user joins a voice call channel. Audio is routed to the speakerphone.
    1. App calls `{props.setEnableSpeakerphoneAPI}(true)`. Audio routing is changed to speakerphone.
    1. User plugs in headset. Audio routing changes to headset.
    1. User unplugs the headset. Audio routing changes to earpiece.

> ℹ️ **Information**
> If the user uses an external audio playback device, such as a Bluetooth headset or a wired headset, setting `{props.setEnableSpeakerphoneAPI}(true)` is invalid and the audio is played through the external device.

## Reference
This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### API reference

- [`setDefaultAudioRoutetoSpeakerphone`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setdefaultaudioroutetospeakerphone)

- [`setEnableSpeakerphone`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_setenablespeakerphone)

- [`onAudioRouteChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengineeventhandler.html#callback_irtcengineeventhandler_onaudioroutingchanged)