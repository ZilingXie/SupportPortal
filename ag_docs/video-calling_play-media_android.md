---
title: Stream media to a channel
description: Play local or online media files locally or to remote users in an Agora
  channel.
sidebar_position: 8
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/play-media?platform=android
exported_on: '2026-01-20T05:56:46.035990Z'
exported_file: play-media_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/play-media?platform=android)

# Stream media to a channel

Playing media files during online business presentations, educational sessions, or casual meetups heightens user engagement. Video SDK enables you to add media playing functionality to your app. 

This page shows you how to use media player-related APIs to play local or online media resources with remote users in Video Calling channels.

 ## Understand the tech

To play a media file in a channel, you open the file using a media player instance. When the file is ready to be played, you set up the local video container to display the media player output. You update channel media options to start publishing the media player stream, and stop publishing the camera and microphone streams. The remote user sees the camera and microphone streams of the media publishing user replaced by media streams.

**Media player flow**

![Media player](https://docs-md.agora.io/images/video-sdk/play-media.svg)

## Prerequisites 

Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implement the logic
To implement a media player in your app, follow these steps:


1. After initializing an `RtcEngine` instance, create an `IMediaPlayer` object and register a player observer by calling the `registerPlayerObserver` method.

   **Java**
   ```java
   mRtcEngine = RtcEngine.create(config);
   
      // Create a media player object
      mediaPlayer = mRtcEngine.createMediaPlayer();
      // Register a player observer
      mediaPlayer.registerPlayerObserver(this);
   ```

   **Kotlin**
   ```kotlin
   mRtcEngine = RtcEngine.create(config)
   
      // Create a media player object
      mediaPlayer = mRtcEngine.createMediaPlayer()
      // Register a player observer
      mediaPlayer.registerPlayerObserver(this)
   ```


1. Implement the callbacks for the media player observer. Observe the player's state through the `onPlayerStateChanged` callback, get the current media file's playback progress through `onPositionChanged`, and handle player events through the `onPlayerEvent` callback.

   **Java**
   ```java
   @Override
       // Observe the player's state
       public void onPlayerStateChanged(io.agora.mediaplayer.Constants.MediaPlayerState mediaPlayerState, io.agora.mediaplayer.Constants.MediaPlayerError mediaPlayerError) {
           Log.e(TAG, "onPlayerStateChanged mediaPlayerState " + mediaPlayerState);
           Log.e(TAG, "onPlayerStateChanged mediaPlayerError " + mediaPlayerError);
           if (mediaPlayerState.equals(PLAYER_STATE_OPEN_COMPLETED)) {
               setMediaPlayerViewEnable(true);
           } else if (mediaPlayerState.equals(PLAYER_STATE_IDLE) || mediaPlayerState.equals(PLAYER_STATE_PLAYBACK_COMPLETED)) {
               setMediaPlayerViewEnable(false);
           }
       }
   
       @Override
       // Observe the current playback progress
       public void onPositionChanged(long position) {
           Log.e(TAG, "onPositionChanged position " + position);
           if (playerDuration > 0) {
               final int result = (int) ((float) position / (float) playerDuration * 100);
               handler.post(new Runnable() {
                   @Override
                   public void run() {
                       progressBar.setProgress(Long.valueOf(result).intValue());
                   }
               });
           }
       }
   
       @Override
       // Observe player events
       public void onPlayerEvent(io.agora.mediaplayer.Constants.MediaPlayerEvent mediaPlayerEvent) {
           Log.e(TAG, " onPlayerEvent mediaPlayerEvent " + mediaPlayerEvent);
       }
   ```

   **Kotlin**
   ```kotlin
   // Observe the player's state
       override fun onPlayerStateChanged(
           mediaPlayerState: io.agora.mediaplayer.Constants.MediaPlayerState,
           mediaPlayerError: io.agora.mediaplayer.Constants.MediaPlayerError
       ) {
           Log.e(TAG, "onPlayerStateChanged mediaPlayerState $mediaPlayerState")
           Log.e(TAG, "onPlayerStateChanged mediaPlayerError $mediaPlayerError")
   
           when (mediaPlayerState) {
               PLAYER_STATE_OPEN_COMPLETED -> setMediaPlayerViewEnable(true)
               PLAYER_STATE_IDLE, PLAYER_STATE_PLAYBACK_COMPLETED -> setMediaPlayerViewEnable(false)
           }
       }
   
       // Observe the current playback progress
       override fun onPositionChanged(position: Long) {
           Log.e(TAG, "onPositionChanged position $position")
   
           if (playerDuration > 0) {
               val result = (position.toFloat() / playerDuration.toFloat() * 100).toInt()
               handler.post {
                   progressBar.progress = result
               }
           }
       }
   
       // Observe player events
       override fun onPlayerEvent(mediaPlayerEvent: io.agora.mediaplayer.Constants.MediaPlayerEvent) {
           Log.e(TAG, "onPlayerEvent mediaPlayerEvent $mediaPlayerEvent")
       }
   ```


1. Call `setupLocalVideo` to render the local media player view.

   **Java**
   ```java
   VideoCanvas videoCanvas = new VideoCanvas(surfaceView, Constants.RENDER_MODE_HIDDEN, Constants.VIDEO_MIRROR_MODE_AUTO,
       Constants.VIDEO_SOURCE_MEDIA_PLAYER, mediaPlayer.getMediaPlayerId(), 0);
       mRtcEngine.setupLocalVideo(videoCanvas);
   ```

   **Kotlin**
   ```kotlin
   val videoCanvas = VideoCanvas(
          surfaceView,
          Constants.RENDER_MODE_HIDDEN,
          Constants.VIDEO_MIRROR_MODE_AUTO,
          Constants.VIDEO_SOURCE_MEDIA_PLAYER,
          mediaPlayer.mediaPlayerId,
          0
      )
   
      mRtcEngine.setupLocalVideo(videoCanvas)
   ```


1. When joining a channel, use `ChannelMediaOptions` to set the media player ID, publish media player audio and video, and share media resources with remote users in the channel.

   **Java**
   ```java
   private ChannelMediaOptions options = new ChannelMediaOptions();
   
       // Set up options
       options.publishMediaPlayerId = mediaPlayer.getMediaPlayerId();
       options.publishMediaPlayerAudioTrack = true;
       options.publishMediaPlayerVideoTrack = true;
   
       // Join the channel
       int res = mRtcEngine.joinChannel(accessToken, channelId, 0, options);
   ```

   **Kotlin**
   ```kotlin
   val options = ChannelMediaOptions()
      
      // Set up options
      options.publishMediaPlayerId = mediaPlayer.mediaPlayerId
      options.publishMediaPlayerAudioTrack = true
      options.publishMediaPlayerVideoTrack = true
      
      // Join the channel
      val res = mRtcEngine.joinChannel(accessToken, channelId, 0, options)
   ```


1. Use the `open` method to open a local or online media file.

   **Java**
   ```java
   mediaPlayer.open(url, 0);
   ```

   **Kotlin**
   ```kotlin
   mediaPlayer.open(url, 0)
   ```


1. Call the `play` method to play the media file.

   **Java**
   ```java
   mediaPlayer.play();
   ```

   **Kotlin**
   ```kotlin
   mediaPlayer?.play()
   ```

    > ⚠️ **Caution**
    > Call the `play` method to play the media file only after receiving the `onPlayerStateChanged` callback reporting the player state as `PLAYER_STATE_OPEN_COMPLETED`.

1. When a user leaves the channel, call `stop` to stop playback, `destroy` to destroy the media player, `unRegisterPlayerObserver` to unregister the player observer, and release allocated resources.

   **Java**
   ```java
   mediaPlayer.stop();
      mediaPlayer.destroy();
      mediaPlayer.unRegisterPlayerObserver(this);
   ```

   **Kotlin**
   ```kotlin
   mediaPlayer.stop()
      mediaPlayer.destroy()
      mediaPlayer.unRegisterPlayerObserver(this)
   ```


## Reference
This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Supported formats and protocols

The media player supports the following media formats and protocols:

#### Video encoding formats

- H.263, H.264, H.265, MPEG-4, MPEG-2, RMVB, Theora, VP3, VP8, AVS, WMV

#### Audio coding formats

- WAV, MP2, MP3, AAC, OPUS, FLAC, Vorbis, AMR-NB, AMR-WB, WMA v1, WMA v2

#### Container formats

- WAV, FLAC, OGG, MOV, ASF, FLV, MP3, MP4, MPEG-TS, Matroska (MKV), AVI, ASS, CONCAT, DTS, AVS

#### Supported protocols

- HTTP, HTTPS, RTMP, HLS, RTP, RTSP

### Sample project

Agora provides an open source sample project [MediaPlayer](https://github.com/AgoraIO/API-Examples/blob/4.2.2/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/MediaPlayer.java) on GitHub. Download it or view the source code for a more detailed example.

### API reference

- [`createMediaPlayer`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_createmediaplayer)
- [`registerPlayerObserver`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_imediaplayer_registerplayersourceobserver)
- [`onPlayerStateChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayersourceobserver.html#callback_imediaplayersourceobserver_onplayersourcestatechanged)
- [`onPositionChanged`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayersourceobserver.html#callback_imediaplayersourceobserver_onpositionchanged)
- [`getMediaPlayerId`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_imediaplayer_registeraudioframeobserver2__parameters)
- [`open`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_imediaplayer_open)
- [`play`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_imediaplayer_play)
- [`pause`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_imediaplayer_pause)
- [`stop`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_imediaplayer_stop)
- [`unRegisterPlayerObserver`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_imediaplayer_unregisterplayersourceobserver)
- [`destroy`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_imediaplayer.html#api_irtcengine_destroymediaplayer)