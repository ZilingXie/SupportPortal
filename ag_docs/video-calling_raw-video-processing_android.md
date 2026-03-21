---
title: Raw video processing
description: Pre and post-process captured video and audio data to achieve the desired
  playback effect.
sidebar_position: 6
platform: android
exported_from: https://docs.agora.io/en/video-calling/advanced-features/raw-video-processing?platform=android
exported_on: '2026-01-20T05:56:48.676351Z'
exported_file: raw-video-processing_android.md
---

[HTML Version](https://docs.agora.io/en/video-calling/advanced-features/raw-video-processing?platform=android)

# Raw video processing

In certain use-cases, it is necessary to process raw video captured through the camera to achieve desired functionality or enhance the user experience. Video SDK provides the capability to pre-process and post-process the captured video data, allowing you to implement custom playback effects.

## Understand the tech

Video SDK enables you to pre-process the captured video frames before sending the data to the encoder or perform post-processing on the received video frames after sending the data to the decoder.

The following figure shows the video data processing flow in the SDK video module.

**Process raw video**

![](https://docs-md.agora.io/images/video-sdk/video-module-data-processing.svg)

* Position (2) corresponds to the `onCaptureVideoFrame` callback.
* Position (3) corresponds to the `onPreEncodeVideoFrame` callback.
* Position (4) corresponds to the`onRenderVideoFrame` callback.

## Prerequisites

Ensure that you have implemented the [SDK quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk.md) in your project.

## Implement raw video processing

To implement raw video data functionality in your project, refer to the following steps:


1. Before joining the channel, create an `IVideoFrameObserver` object and register the video observer by calling the `registerVideoFrameObserver` method.

**Java**
```java
    int ret = engine.registerVideoFrameObserver(iVideoFrameObserver);
```

**Kotlin**
```kotlin
   val ret = engine.registerVideoFrameObserver(iVideoFrameObserver)
```


1. Implement the `onCaptureVideoFrame` and `onRenderVideoFrame` callbacks. After obtaining the video data, process it according to your specific use-case.

**Java**
```java
private final IVideoFrameObserver iVideoFrameObserver = new IVideoFrameObserver() {
    @Override
    public boolean onCaptureVideoFrame(VideoFrame videoFrame) {
        Log.i(TAG, "OnEncodedVideoImageReceived" + \Thread.currentThread().getName());
        if (isSnapshot) {
            isSnapshot = false;
            
            // Get the image bitmap
            VideoFrame.Buffer buffer = videoFrame.getBuffer();
            VideoFrame.I420Buffer i420Buffer = buffer.toI420();
            int width = i420Buffer.getWidth();
            int height = i420Buffer.getHeight();
            
            ByteBuffer bufferY = i420Buffer.getDataY();
            ByteBuffer bufferU = i420Buffer.getDataU();
            ByteBuffer bufferV = i420Buffer.getDataV();
            
            byte[] i420 = YUVUtils.toWrappedI420(bufferY, bufferU, bufferV, width, height);
            
            Bitmap bitmap = YUVUtils.NV21ToBitmap(getContext(),
                YUVUtils.I420ToNV21(i420, width, height),
                width,
                height);
            
            Matrix matrix = new Matrix();
            matrix.setRotate(270);
            // Rotate around the center
            Bitmap newBitmap = Bitmap.createBitmap(bitmap, 0, 0, width, height, matrix, false);
            // Save to file
            saveBitmap2Gallery(newBitmap);
            
            bitmap.recycle();
            i420Buffer.release();
        }
        return false;
    }
    
    @Override
    public boolean onScreenCaptureVideoFrame(VideoFrame videoFrame) {
        return false;
    }
    
    @Override
    public boolean onMediaPlayerVideoFrame(VideoFrame videoFrame, int i) {
        return false;
    }
    
    @Override
    public boolean onRenderVideoFrame(String s, int i, VideoFrame videoFrame) {
        return false;
    }
    
    @Override
    public int getVideoFrameProcessMode() {
        return 0;
    }
    
    @Override
    public int getVideoFormatPreference() {
        return 1;
    }
    
    @Override
    public int getRotationApplied() {
        return 0;
    }
    
    @Override
    public boolean getMirrorApplied() {
        return false;
    }
};
```

**Kotlin**
```kotlin
private val iVideoFrameObserver = object : IVideoFrameObserver {
   override fun onCaptureVideoFrame(videoFrame: VideoFrame): Boolean {
       Log.i(TAG, "OnEncodedVideoImageReceived\${\Thread.currentThread().name\}")
       if (isSnapshot) {
           isSnapshot = false

           // Get the image bitmap
           val buffer = videoFrame.buffer
           val i420Buffer = buffer.toI420()
           val width = i420Buffer.width
           val height = i420Buffer.height

           val bufferY = i420Buffer.dataY
           val bufferU = i420Buffer.dataU
           val bufferV = i420Buffer.dataV

           val i420 = YUVUtils.toWrappedI420(bufferY, bufferU, bufferV, width, height)

           val bitmap = YUVUtils.NV21ToBitmap(
               context = context,
               nv21Data = YUVUtils.I420ToNV21(i420, width, height),
               width = width,
               height = height
           )

           val matrix = Matrix().apply { setRotate(270f) }
           // Rotate around the center
           val newBitmap = Bitmap.createBitmap(bitmap, 0, 0, width, height, matrix, false)

           // Save to file
           saveBitmap2Gallery(newBitmap)

           bitmap.recycle()
           i420Buffer.release()
       }
       return false
   }

   override fun onScreenCaptureVideoFrame(videoFrame: VideoFrame): Boolean {
       return false
   }

   override fun onMediaPlayerVideoFrame(videoFrame: VideoFrame, i: Int): Boolean {
       return false
   }

   override fun onRenderVideoFrame(s: String, i: Int, videoFrame: VideoFrame): Boolean {
       return false
   }

   override fun getVideoFrameProcessMode(): Int {
       return 0
   }

   override fun getVideoFormatPreference(): Int {
       return 1
   }

   override fun getRotationApplied(): Int {
       return 0
   }

   override fun getMirrorApplied(): Boolean {
       return false
   }
}
```

    > ⚠️ **Caution**
    > When modifying parameters in a `VideoFrame`, ensure that the updated parameters match the actual video frame in the buffer. Mismatches may cause issues like unexpected rotation, distortion, or other visual problems in the local preview and the remote video.

## Reference

This section contains content that completes the information on this page, or points you to documentation that explains other aspects to this product.

### Sample project

Agora provides an open source sample project [ProcessRawData](https://github.com/AgoraIO/API-Examples/blob/main/Android/APIExample/app/src/main/java/io/agora/api/example/examples/advanced/ProcessRawData.java) on GitHub. Download it or view the source code for a more detailed example.

### API reference

- [`registerVideoFrameObserver`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_imediaengine_registervideoframeobserver)
- [`registerVideoEncodedFrameObserver`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_irtcengine.html#api_imediaengine_registervideoencodedframeobserver)
- [`onCaptureVideoFrame`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ivideoframeobserver.html#callback_ivideoframeobserver_oncapturevideoframe)
- [`onRenderVideoFrame`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ivideoframeobserver.html#callback_ivideoframeobserver_onrendervideoframe)
- [`getVideoFrameProcessMode`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ivideoframeobserver.html#callback_ivideoframeobserver_getvideoframeprocessmode)
- [`onEncodedVideoFrameReceived`](https://api-ref.agora.io/en/video-sdk/android/4.x/API/class_ivideoencodedframeobserver.html#callback_ivideoencodedframeobserver_onencodedvideoframereceived)