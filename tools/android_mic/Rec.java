import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import java.io.FileOutputStream;

/** Record N seconds of 16 kHz mono PCM from the microphone to a file (run as root via app_process). */
public class Rec {
    public static void main(String[] a) throws Exception {
        String out = a[0];
        int secs = Integer.parseInt(a[1]);
        int rate = 16000;
        int min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord r = new AudioRecord(MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, Math.max(min, 16384));
        if (r.getState() != AudioRecord.STATE_INITIALIZED) { System.out.println("init-failed"); System.exit(2); }
        FileOutputStream f = new FileOutputStream(out);
        byte[] buf = new byte[4096];
        long end = System.currentTimeMillis() + secs * 1000L;
        r.startRecording();
        long total = 0;
        while (System.currentTimeMillis() < end) {
            int n = r.read(buf, 0, buf.length);
            if (n > 0) { f.write(buf, 0, n); total += n; }
        }
        r.stop(); r.release(); f.close();
        System.out.println("bytes " + total);
    }
}
