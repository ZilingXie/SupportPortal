---
title: Custom video source
description: Integrate a custom video or audio capture into your client
sidebar_position: 4
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/custom-video?platform=android
exported_on: '2026-01-20T05:56:29.464103Z'
exported_file: custom-video_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/custom-video?platform=android)

# Custom video source

Custom video capture refers to the collection of a video stream from a custom source. Unlike the default video capture method, custom video capture enables you to control the capture source, and precisely adjust video attributes. You can dynamically adjust parameters such as video quality, resolution, and frame rate to adapt to various application use-cases. For example, you can capture video from high-definition cameras, and drone cameras.

Agora recommends default video capture for its stability, reliability, and ease of integration. Custom video capture offers flexibility and customization for specific video capture use-cases where default video capture does not fulfill your requirements.

## Understand the tech

Video SDK provides a custom video track method for video self-collection. You can create and publish custom video tracks to one or more channels. You use the self-capture module to drive the capture device, and send the captured video frames to the SDK through the video track.

The following figure illustrates the video data transmission process when custom video capture is implemented:

![Custom video source](https://docs-md.agora.io/images/video-sdk/publish-custom-tracks-in-a-channel.svg)

## Prerequisites

Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implement the logic

This section shows you how to implement custom video capture and custom video rendering in your app.


### Custom video capture

The following figure shows the workflow you implement to capture and stream a custom video source in your app.

**Custom video capture**

![API call sequence](https://docs-md.agora.io/images/video-sdk/custom-video-capture.svg)

Take the following steps to implement this workflow:

1. Create a custom video track

   To create a custom video track and obtain the video track ID, call `createCustomVideoTrack` after initializing an instance of `RtcEngine`. To create multiple custom video tracks, call the method multiple times.

   **Java**
   ```java
   int videoTrackId = RtcEngine.createCustomVideoTrack();
   ```

   **Kotlin**
   ```kotlin
   val videoTrackId = RtcEngine.createCustomVideoTrack()
   ```


1. Join a channel and publish the custom video track

   **Java**
   ```java
   // Create a ChannelMediaOptions instance
       ChannelMediaOptions option = new ChannelMediaOptions();
       // Set the client role to BROADCASTER
       option.clientRoleType = Constants.CLIENT_ROLE_BROADCASTER;
       // Enable auto subscription of audio and video
       option.autoSubscribeAudio = true;
       option.autoSubscribeVideo = true;
       // Publish self-captured video stream
       option.publishCustomVideoTrack = true;
       // Set custom video track ID
       option.customVideoTrackId = videoTrackId;
       // Join a channel with the specified options
       int res = engine.joinChannel(accessToken, channelId, 0, option);
   ```

   **Kotlin**
   ```kotlin
   // Create a ChannelMediaOptions instance
      val option = ChannelMediaOptions().apply {
          // Set the client role to BROADCASTER
          clientRoleType = Constants.CLIENT_ROLE_BROADCASTER
          // Enable auto subscription of audio and video
          autoSubscribeAudio = true
          autoSubscribeVideo = true
          // Publish self-captured video stream
          publishCustomVideoTrack = true
          // Set custom video track ID
          customVideoTrackId = videoTrackId
      }
   
      // Join a channel with the specified options
      val res = engine.joinChannel(accessToken, channelId, 0, option)
   ```


1. Implement your self-capture module

    Agora provides the [VideoFileReader](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/utils/VideoFileReader.java) demo project that shows you how to read `YUV` format video data from a local file. In a production environment, create a custom video module for your device using Video SDK based on your business requirements.

1. Push video data to the SDK 

    Before sending captured video frames to Video SDK, integrate your video module with the `VideoFrame`. To ensure audio-video synchronization, best practice is to obtain the current monotonic time from Video SDK and pass it as the timestamp parameter in the `VideoFrame`.

    > ℹ️ **Information**
    > To ensure audio-video synchronization, set the timestamp parameter of `VideoFrame` to the system's Monotonic Time. Use `getCurrentMonotonicTimeInMs` to obtain the current monotonic Time.

    Call `pushExternalVideoFrameById` [2/2] to push the captured video frames through the video track to Video SDK. Ensure that the `videoTrackId` matches the track ID you specified when joining the channel. Customize parameters like pixel format, data type, and timestamp in the `VideoFrame`.

    The following code samples demonstrate pushing `I420`, `NV21`, `NV12`, and `Texture` format video data:
    
   **I420**

**Java**
   ```java
   private void pushVideoFrameByI420(int trackId, byte[] yuv, int width, int height) {
         // Create an i420Buffer object and store the original YUV data in the buffer
         JavaI420Buffer i420Buffer = JavaI420Buffer.allocate(width, height);
         i420Buffer.getDataY().put(yuv, 0, i420Buffer.getDataY().limit());
         i420Buffer.getDataU().put(yuv, i420Buffer.getDataY().limit(), i420Buffer.getDataU().limit());
         i420Buffer.getDataV().put(yuv, i420Buffer.getDataY().limit() + i420Buffer.getDataU().limit(), i420Buffer.getDataV().limit());
         // Get the current monotonic time from the SDK
         long currentMonotonicTimeInMs = engine.getCurrentMonotonicTimeInMs();
         // Create a VideoFrame object, passing the I420 video frame to be pushed and the monotonic time of the video frame (in nanoseconds)
         VideoFrame videoFrame = new VideoFrame(i420Buffer, 0, currentMonotonicTimeInMs * 1000000);
   
         // Push the video frame to the SDK through the video track
         int ret = engine.pushExternalVideoFrameById(videoFrame, trackId);
         // Release the memory resources occupied by the i420Buffer object
         i420Buffer.release();
   
         if (ret != Constants.ERR_OK) {
            Log.w(TAG, "pushExternalVideoFrame error");
         }
      }
   ```

   **Kotlin**
   ```kotlin
   private fun pushVideoFrameByI420(trackId: Int, yuv: ByteArray, width: Int, height: Int) {
         // Create an i420Buffer object and store the original YUV data in the buffer
         val i420Buffer = JavaI420Buffer.allocate(width, height)
         i420Buffer.getDataY().put(yuv, 0, i420Buffer.getDataY().limit())
         i420Buffer.getDataU().put(yuv, i420Buffer.getDataY().limit(), i420Buffer.getDataU().limit())
         i420Buffer.getDataV().put(yuv, i420Buffer.getDataY().limit() + i420Buffer.getDataU().limit(), i420Buffer.getDataV().limit())
                  
         // Get the current monotonic time from the SDK
         val currentMonotonicTimeInMs = engine.getCurrentMonotonicTimeInMs()
               
         // Create a VideoFrame object, passing the I420 video frame to be pushed and the monotonic time of the video frame (in nanoseconds)
         val videoFrame = VideoFrame(i420Buffer, 0, currentMonotonicTimeInMs * 1_000_000)
               
         // Push the video frame to the SDK through the video track
         val ret = engine.pushExternalVideoFrameById(videoFrame, trackId)
               
         // Release the memory resources occupied by the i420Buffer object
         i420Buffer.release()
               
         if (ret != Constants.ERR_OK) {
            Log.w(TAG, "pushExternalVideoFrame error")
         }
      }
   ```
      
   **NV21**

**Java**
   ```java
   private void pushVideoFrameByNV21(int trackId, byte[] nv21, int width, height) {
         // Create a frameBuffer object and store the original YUV data in the NV21 format buffer
         VideoFrame.Buffer frameBuffer = new NV21Buffer(nv21, width, height, null);
   
         // Get the current monotonic time from the SDK
         long currentMonotonicTimeInMs = engine.getCurrentMonotonicTimeInMs();
         // Create a VideoFrame object, pass the NV21 video frame to be pushed and the monotonic time of the video frame (in nanoseconds)
         VideoFrame videoFrame = new VideoFrame(frameBuffer, 0, currentMonotonicTimeInMs * 1000000);
   
         // Push the video frame to the SDK through the video track
         int ret = engine.pushExternalVideoFrameById(videoFrame, trackId);
   
         if (ret != Constants.ERR_OK) {
            Log.w(TAG, "pushExternalVideoFrame error");
         }
      }
   ```

   **Kotlin**
   ```kotlin
   private fun pushVideoFrameByNV21(trackId: Int, nv21: ByteArray, width: Int, height: Int) {
         // Create a frameBuffer object and store the original YUV data in the NV21 format buffer
         val frameBuffer: VideoFrame.Buffer = NV21Buffer(nv21, width, height, null)
      
         // Get the current monotonic time from the SDK
         val currentMonotonicTimeInMs = engine.getCurrentMonotonicTimeInMs()
      
         // Create a VideoFrame object, pass the NV21 video frame to be pushed and the monotonic time of the video frame (in nanoseconds)
         val videoFrame = VideoFrame(frameBuffer, 0, currentMonotonicTimeInMs * 1_000_000)
      
         // Push the video frame to the SDK through the video track
         val ret = engine.pushExternalVideoFrameById(videoFrame, trackId)
               
         if (ret != Constants.ERR_OK) {
            Log.w(TAG, "pushExternalVideoFrame error")
         }
      }
   ```
   **NV12**

**Java**
   ```java
   private void pushVideoFrameByNV12(int trackId, ByteBuffer nv12, int width, int height) {
         // Create a frameBuffer object and store the original YUV data in the NV12 format buffer
         VideoFrame.Buffer frameBuffer = new NV12Buffer(width, height, width, height, nv12, null);
         // Get the current monotonic time from the SDK
         long currentMonotonicTimeInMs = engine.getCurrentMonotonicTimeInMs();
         // Create a VideoFrame object, pass the NV12 video frame to be pushed and the monotonic time of the video frame (in nanoseconds)
         VideoFrame videoFrame = new VideoFrame(frameBuffer, 0, currentMonotonicTimeInMs * 1000000);
         
         // Push the video frame to the SDK through the video track
         int ret = engine.pushExternalVideoFrameById(videoFrame, trackId);
         if (ret != Constants.ERR_OK) {
               Log.w(TAG, "pushExternalVideoFrame error");
         }
      }
   ```

   **Kotlin**
   ```kotlin
   private fun pushVideoFrameByNV12(trackId: Int, nv12: ByteBuffer, width: Int, height: Int) {
         // Create a frameBuffer object and store the original YUV data in the NV12 format buffer
         val frameBuffer: VideoFrame.Buffer = NV12Buffer(width, height, width, height, nv12, null)
         // Get the current monotonic time from the SDK
         val currentMonotonicTimeInMs = engine.getCurrentMonotonicTimeInMs()
         // Create a VideoFrame object, pass the NV12 video frame to be pushed and the monotonic time of the video frame (in nanoseconds)
         val videoFrame = VideoFrame(frameBuffer, 0, currentMonotonicTimeInMs * 1_000_000)
         // Push the video frame to the SDK through the video track
         val ret = engine.pushExternalVideoFrameById(videoFrame, trackId)
            
         if (ret != Constants.ERR_OK) {
            Log.w(TAG, "pushExternalVideoFrame error")
         }
      }
   ```
   **Texture**

**Java**
   ```java
   private void pushVideoFrameByTexture(int trackId, int textureId, VideoFrame.TextureBuffer.Type textureType, int width, int height) {
         // Create a frameBuffer object to store the texture format video frame
         VideoFrame.Buffer frameBuffer = new TextureBuffer(
            EglBaseProvider.getCurrentEglContext(),
            width,
            height,
            textureType,
            textureId,
            new Matrix(),
            null,
            null,
            null
         );
         // Get the current monotonic time from the SDK
         long currentMonotonicTimeInMs = engine.getCurrentMonotonicTimeInMs();
         // Create a VideoFrame object, passing the texture video frame to be pushed and the monotonic time of the video frame (in nanoseconds)
         VideoFrame videoFrame = new VideoFrame(frameBuffer, 0, currentMonotonicTimeInMs * 1000000);
         // Push the video frame to the SDK through the video track
         int ret = engine.pushExternalVideoFrameById(videoFrame, trackId);
         if (ret != Constants.ERR_OK) {
            Log.w(TAG, "pushExternalVideoFrame error");
         }
      }
   ```

   **Kotlin**
   ```kotlin
   private fun pushVideoFrameByTexture(
            trackId: Int,
            textureId: Int,
            textureType: VideoFrame.TextureBuffer.Type,
            width: Int,
            height: Int
         ) {
         // Create a frameBuffer object to store the texture format video frame
         val frameBuffer: VideoFrame.Buffer = TextureBuffer(
            EglBaseProvider.getCurrentEglContext(),
            width,
            height,
            textureType,
            textureId,
            Matrix(),
            null,
            null,
            null
         )
   
         // Get the current monotonic time from the SDK
         val currentMonotonicTimeInMs = engine.getCurrentMonotonicTimeInMs()
         // Create a VideoFrame object, passing the texture video frame to be pushed and the monotonic time of the video frame (in nanoseconds)
         val videoFrame = VideoFrame(frameBuffer, 0, currentMonotonicTimeInMs * 1_000_000)
         // Push the video frame to the SDK through the video track
         val ret = engine.pushExternalVideoFrameById(videoFrame, trackId)
   
         if (ret != Constants.ERR_OK) {
            Log.w(TAG, "pushExternalVideoFrame error")
         }
      }
   ```

   > ℹ️ **Information**
   > If the captured custom video format is Texture and remote users experience flickering or distortion in the captured video, it is recommended to first duplicate the video data and then send both the original and duplicated video data back to the Video SDK. This helps eliminate anomalies during internal data encoding processes.

5. Destroy custom video tracks

    To stop custom video capture and destroy the video track, call `destroyCustomVideoTrack`. 

   **Java**
   ```java
   // Destroy custom video track
      engine.destroyCustomVideoTrack(videoTrack);
      // Leave the channel
      engine.leaveChannelEx(connection);
   ```

   **Kotlin**
   ```kotlin
   // Destroy custom video track
      engine.destroyCustomVideoTrack(videoTrack)
      // Leave the channel
      engine.leaveChannelEx(connection)
   ```


### Custom video rendering

To implement custom video rendering in your app, refer to the following steps:

1. Set up `onCaptureVideoFrame` or `onRenderVideoFrame` callback to obtain the video data to be played.
1. Implement video rendering and playback yourself.

## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Applicable use-cases

Use custom video capture in the following industries and use-cases:

**Specialized video processing and enhancement**

In specific gaming or virtual reality use-cases, real-time effects processing, filter handling, or other enhancement effects necessitate direct access to the original video stream. Custom video capture facilitates this, enabling seamless real-time processing and enhances the overall gaming or virtual reality experience for a more realistic outcome.

**High-precision video capture**

In video surveillance applications, detailed observation and analysis of scene details is necessary. Custom video capture enables higher image quality and finer control over capture to meet the requirements of video monitoring.

**Capture from specific video sources**

Industries such as IoT and live streaming often require the use of specific cameras, monitoring devices, or non-camera video sources, such as video capture cards or screen recording data. In such situations, default Video SDK capture may not meet your requirements, necessitating use of custom video capture.

**Seamless integration with specific devices or third-party applications**

In smart home or IoT applications, transmitting video from devices to users' smartphones or computers for monitoring and control may require the use of specific devices or applications for video capture. Custom video capture facilitates seamless integration of specific devices or applications with the Video SDK.

**Specific video encoding formats**

In certain live streaming use-cases, specific video encoding formats may be needed to meet business requirements. In such cases, Video SDK default capture might not suffice, and custom video capture is required to capture and encode videos in specific formats.

### Advantages

Using custom video capture offers the following advantages:

**More types of video streams**

Custom video capture allows the use of higher quality and a greater variety of capture devices and cameras, resulting in clearer and smoother video streams. This enhances the user viewing experience and makes the product more competitive.

**More flexible video effects**

Custom video capture enables you to implement richer and more personalized video effects and filters, enhancing the user experience. You can implement effects such as beautification filters and dynamic stickers.

**Adaptation to diverse use-case requirements**

Custom video capture helps applications better adapt to the requirements of various use-cases, such as live streaming, video conferencing, and online education. You can customize different video capture solutions based on the use-case requirements to provide a more robust application.

### Sample projects

Agora provides the following open-source sample projects for your reference. Download the project or view the source code for a more detailed example.

 * [MultiVideoSourceTracks](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/MultiVideoSourceTracks.java): Video self-capture
* [CustomRemoteVideoRender](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/CustomRemoteVideoRender.java): Custom remote video rendering

### API reference

- [`createCustomVideoTrack`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_createcustomvideotrack)
- [`destroyCustomVideoTrack`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_destroycustomvideotrack)
- [`getCurrentMonotonicTimeInMs`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_getcurrentmonotonictimeinms)
- [`joinChannel`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_joinchannel)
- [`pushExternalVideoFrameById` [2/2]](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_irtcengine_pushvideoframe3)
- [`onCaptureVideoFrame`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ivideoframeobserver.html#callback_ivideoframeobserver_oncapturevideoframe)
- [`onRenderVideoFrame`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ivideoframeobserver.html#callback_ivideoframeobserver_onrendervideoframe)