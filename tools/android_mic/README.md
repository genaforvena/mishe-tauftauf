# Android microphone recorder (no Android app needed)

`Rec.java` records N seconds of 16 kHz mono 16-bit PCM from the microphone. It needs
RECORD_AUDIO, which the adb shell user lacks, so run it as root (a rooted device).
Verified on a Galaxy Note 3 (Android 5.0), which has no tinycap/arecord.

Build (any Android SDK; SDK and PHONE are yours):

    javac -source 8 -target 8 -cp $SDK/platforms/android-34/android.jar Rec.java
    $SDK/build-tools/35.0.0/d8 --min-api 21 --lib $SDK/platforms/android-34/android.jar --output . Rec.class
    python3 -c "import zipfile;z=zipfile.ZipFile('rec.jar','w');z.write('classes.dex');z.close()"
    adb -s $PHONE push rec.jar /data/local/tmp/rec.jar

Run, pull through stdout, delete at once:

    adb -s $PHONE shell 'su -c "cd /data/local/tmp && app_process -Djava.class.path=/data/local/tmp/rec.jar /system/bin Rec /data/local/tmp/a.pcm 6"'
    adb -s $PHONE shell 'su -c "cat /data/local/tmp/a.pcm"' > a.pcm
    adb -s $PHONE shell 'su -c "rm -f /data/local/tmp/a.pcm"'

Speaking through the phone is a different path: a command-line process cannot get an
Android context (TextToSpeech init crashes), so speak by synthesizing a wav on a host
and playing it with a media intent, or add a speak action to an app.
Audio is transient: process it, keep derived numbers, delete the file.
