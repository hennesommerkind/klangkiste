package de.klangkiste.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.Icon;
import android.media.MediaMetadata;
import android.media.VolumeProvider;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Fernbedienung für Sperrbildschirm und Benachrichtigungsleiste.
 * Hört über /api/events, was die Box gerade spielt, und zeigt es als Medien-
 * Benachrichtigung mit Zurück, Play/Pause und Vor. Die Lautstärketasten des
 * Handys stellen dabei die Lautstärke der Box. Der Ton bleibt auf der Box.
 * Beendet sich von selbst, wenn längere Zeit nichts läuft.
 */
public class ControlService extends Service {
    static final String EXTRA_URL = "url";
    private static final String CHANNEL = "steuerung";
    private static final int NOTE_ID = 1;
    private static final String ACT_PREV = "de.klangkiste.app.PREV";
    private static final String ACT_TOGGLE = "de.klangkiste.app.TOGGLE";
    private static final String ACT_NEXT = "de.klangkiste.app.NEXT";
    private static final String ACT_CLOSE = "de.klangkiste.app.CLOSE";
    private static final long STOP_AFTER_IDLE = 5 * 60_000L;     // gestoppt / nicht erreichbar
    private static final long STOP_AFTER_PAUSE = 30 * 60_000L;   // pausiert

    private final Handler ui = new Handler(Looper.getMainLooper());
    private MediaSession session;
    private VolumeProvider volume;
    private volatile String base;
    private volatile boolean running;
    private Thread reader;
    private volatile HttpURLConnection stream;

    // Zustand der Box
    private String state = "offline";
    private String title = "", artist = "", folder = "", coverKey = "";
    private double elapsed, duration;
    private int vol, vmax = 100;
    private Bitmap cover;
    private long idleSince = System.currentTimeMillis();

    // ---------- Lebenszyklus ----------
    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel ch = new NotificationChannel(CHANNEL, "Steuerung der Klangkiste", NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Was die Box gerade spielt, mit Zurück, Play/Pause und Vor – auch auf dem Sperrbildschirm.");
        ch.setShowBadge(false);
        ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
        nm.createNotificationChannel(ch);

        session = new MediaSession(this, "Klangkiste");
        session.setCallback(new MediaSession.Callback() {
            @Override public void onPlay() { send("play", null); }
            @Override public void onPause() { send("pause", null); }
            @Override public void onStop() { send("pause", null); }
            @Override public void onSkipToNext() { send("next", null); }
            @Override public void onSkipToPrevious() { send("prev", null); }
            @Override public void onSeekTo(long ms) { send("seek", ms / 1000.0); }
        });
        Intent open = new Intent(this, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        session.setSessionActivity(PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE));
        useVolume(vmax);
        session.setActive(true);
        ui.postDelayed(watchdog, 30_000);
    }

    /** Lautstärketasten des Handys stellen die Box (0 … Höchstlautstärke der Box). */
    private void useVolume(int max) {
        volume = new VolumeProvider(VolumeProvider.VOLUME_CONTROL_ABSOLUTE, max, Math.min(vol, max)) {
            @Override public void onSetVolumeTo(int v) { setCurrentVolume(v); vol = v; send("volume", v); }
            @Override public void onAdjustVolume(int dir) {
                if (dir == 0) return;
                vol = Math.max(0, Math.min(getMaxVolume(), vol + dir * Math.max(1, getMaxVolume() / 20)));
                setCurrentVolume(vol);
                send("volume", vol);
            }
        };
        session.setPlaybackToRemote(volume);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Innerhalb von 5 s muss eine Benachrichtigung stehen
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTE_ID, build(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(NOTE_ID, build());
        }
        String a = intent != null ? intent.getAction() : null;
        if (ACT_PREV.equals(a)) send("prev", null);
        else if (ACT_NEXT.equals(a)) send("next", null);
        else if (ACT_TOGGLE.equals(a)) send("play_pause", null);
        else if (ACT_CLOSE.equals(a)) { shutdown(); return START_NOT_STICKY; }

        String url = intent != null ? intent.getStringExtra(EXTRA_URL) : null;
        if (url != null && !url.equals(base)) {
            base = url;
            restartReader();
        } else if (reader == null && base != null) {
            restartReader();
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        closeStream();
        ui.removeCallbacksAndMessages(null);
        session.setActive(false);
        session.release();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent i) { return null; }

    private void shutdown() {
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    /** Läuft länger nichts, verschwindet die Steuerung wieder. */
    private final Runnable watchdog = new Runnable() {
        @Override public void run() {
            long idle = System.currentTimeMillis() - idleSince;
            boolean playing = "play".equals(state);
            if (!playing && idle > ("pause".equals(state) ? STOP_AFTER_PAUSE : STOP_AFTER_IDLE)) {
                shutdown();
                return;
            }
            ui.postDelayed(this, 30_000);
        }
    };

    // ---------- Verbindung zur Box (Server-Sent Events) ----------
    private void restartReader() {
        running = false;
        closeStream();
        running = true;
        final String url = base;
        reader = new Thread(() -> {
            long wait = 2000;
            while (running && url.equals(base)) {
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url + "/api/events").openConnection();
                    c.setConnectTimeout(4000);
                    c.setReadTimeout(45_000);   // die Box schickt alle 20 s ein Lebenszeichen
                    c.setRequestProperty("Accept", "text/event-stream");
                    stream = c;
                    BufferedReader in = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8));
                    wait = 2000;
                    String line, event = "";
                    StringBuilder data = new StringBuilder();
                    while (running && (line = in.readLine()) != null) {
                        if (line.startsWith("event:")) event = line.substring(6).trim();
                        else if (line.startsWith("data:")) data.append(line.substring(5).trim());
                        else if (line.isEmpty()) {
                            if ("state".equals(event) && data.length() > 0) {
                                final JSONObject j = new JSONObject(data.toString());
                                ui.post(() -> apply(j));
                            }
                            event = "";
                            data.setLength(0);
                        }
                    }
                } catch (Exception e) {
                    // Box nicht erreichbar – gleich noch einmal
                } finally {
                    closeStream();
                }
                if (!running) break;
                ui.post(() -> { if (!"offline".equals(state)) { state = "offline"; idleSince = System.currentTimeMillis(); refresh(); } });
                try { Thread.sleep(wait); } catch (InterruptedException ie) { break; }
                wait = Math.min(wait * 2, 30_000);
            }
        }, "klangkiste-events");
        reader.start();
    }

    private void closeStream() {
        HttpURLConnection c = stream;
        stream = null;
        if (c != null) try { c.disconnect(); } catch (Exception ignored) { }
    }

    private void apply(JSONObject j) {
        String old = state;
        state = j.optString("state", "stop");
        if (!state.equals(old) && !"play".equals(state)) idleSince = System.currentTimeMillis();
        title = j.optString("title", "");
        artist = j.optString("artist", "");
        folder = j.isNull("folder") ? "" : j.optString("folder", "");
        elapsed = j.optDouble("elapsed", 0);
        duration = j.optDouble("duration", 0);
        vol = j.optInt("volume", vol);
        int mx = Math.max(1, j.optInt("vmax", vmax));
        if (mx != vmax) { vmax = mx; useVolume(vmax); }
        String coverUrl = j.isNull("cover_url") ? "" : j.optString("cover_url", "");
        String key = !coverUrl.isEmpty() ? coverUrl : folder;
        if (!key.equals(coverKey)) {
            coverKey = key;
            cover = null;
            loadCover(key, coverUrl);
        }
        refresh();
    }

    private void loadCover(String key, String coverUrl) {
        if (key.isEmpty()) return;
        final String url;
        try {
            url = !coverUrl.isEmpty() ? coverUrl : base + "/api/cover?folder=" + URLEncoder.encode(folder, "UTF-8");
        } catch (Exception e) { return; }
        new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                c.setConnectTimeout(4000);
                c.setReadTimeout(8000);
                if (c.getResponseCode() == 200) {
                    try (InputStream in = c.getInputStream()) {
                        Bitmap b = BitmapFactory.decodeStream(in);
                        if (b != null) {
                            int max = 512;
                            if (b.getWidth() > max) b = Bitmap.createScaledBitmap(b, max, b.getHeight() * max / b.getWidth(), true);
                            final Bitmap fb = b;
                            ui.post(() -> { if (key.equals(coverKey)) { cover = fb; refresh(); } });
                        }
                    }
                }
                c.disconnect();
            } catch (Exception ignored) { }
        }).start();
    }

    // ---------- Befehle an die Box ----------
    private void send(String action, Object value) {
        final String url = base;
        if (url == null) return;
        // Sofort anzeigen, nicht auf die Antwort warten
        if ("play".equals(action) || ("play_pause".equals(action) && !"play".equals(state))) { state = "play"; refresh(); }
        else if ("pause".equals(action) || "play_pause".equals(action)) { state = "pause"; idleSince = System.currentTimeMillis(); refresh(); }
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject().put("action", action);
                if (value != null) body.put("value", value);
                HttpURLConnection c = (HttpURLConnection) new URL(url + "/api/player").openConnection();
                c.setConnectTimeout(4000);
                c.setReadTimeout(10_000);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                try (OutputStream o = c.getOutputStream()) { o.write(body.toString().getBytes(StandardCharsets.UTF_8)); }
                c.getResponseCode();
                c.disconnect();
            } catch (Exception ignored) { }
        }).start();
    }

    // ---------- Anzeige ----------
    private void refresh() {
        boolean playing = "play".equals(state);
        boolean offline = "offline".equals(state);
        int ps = playing ? PlaybackState.STATE_PLAYING
                : "pause".equals(state) ? PlaybackState.STATE_PAUSED
                : offline ? PlaybackState.STATE_NONE : PlaybackState.STATE_STOPPED;
        long actions = PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_PLAY_PAUSE
                | PlaybackState.ACTION_SKIP_TO_NEXT | PlaybackState.ACTION_SKIP_TO_PREVIOUS | PlaybackState.ACTION_STOP;
        if (duration > 0) actions |= PlaybackState.ACTION_SEEK_TO;
        session.setPlaybackState(new PlaybackState.Builder()
                .setActions(actions)
                .setState(ps, (long) (elapsed * 1000), playing ? 1f : 0f)
                .build());

        MediaMetadata.Builder m = new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, shownTitle())
                .putString(MediaMetadata.METADATA_KEY_ARTIST, shownArtist())
                .putString(MediaMetadata.METADATA_KEY_ALBUM, leaf(folder));
        if (duration > 0) m.putLong(MediaMetadata.METADATA_KEY_DURATION, (long) (duration * 1000));
        if (cover != null) m.putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, cover);
        session.setMetadata(m.build());

        volume.setCurrentVolume(Math.min(vol, vmax));

        getSystemService(NotificationManager.class).notify(NOTE_ID, build());
    }

    private String shownTitle() {
        if ("offline".equals(state)) return base == null ? "Verbinde mit der Klangkiste …" : "Klangkiste nicht erreichbar";
        if (!title.isEmpty()) return title;
        if (!folder.isEmpty()) return leaf(folder);
        return "stop".equals(state) ? "Gerade läuft nichts" : "Klangkiste";
    }

    private String shownArtist() {
        if ("offline".equals(state)) return "Neuer Versuch läuft";
        if (!artist.isEmpty()) return artist;
        return !folder.isEmpty() && !title.isEmpty() ? leaf(folder) : "Klangkiste";
    }

    private static String leaf(String f) {
        int i = f.lastIndexOf('/');
        return i >= 0 ? f.substring(i + 1) : f;
    }

    private Notification build() {
        boolean playing = "play".equals(state);
        Notification.Builder b = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(shownTitle())
                .setContentText(shownArtist())
                .setLargeIcon(cover)
                .setOngoing(playing)
                .setShowWhen(false)
                .setOnlyAlertOnce(true)
                .setCategory(Notification.CATEGORY_TRANSPORT)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setContentIntent(session.getController().getSessionActivity())
                .setDeleteIntent(pending(ACT_CLOSE))
                .addAction(action(android.R.drawable.ic_media_previous, "Zurück", ACT_PREV))
                .addAction(action(playing ? android.R.drawable.ic_media_pause : android.R.drawable.ic_media_play,
                        playing ? "Pause" : "Abspielen", ACT_TOGGLE))
                .addAction(action(android.R.drawable.ic_media_next, "Vor", ACT_NEXT))
                .setStyle(new Notification.MediaStyle()
                        .setMediaSession(session.getSessionToken())
                        .setShowActionsInCompactView(0, 1, 2));
        if (Build.VERSION.SDK_INT >= 31) b.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        return b.build();
    }

    private Notification.Action action(int icon, String label, String act) {
        return new Notification.Action.Builder(Icon.createWithResource(this, icon), label, pending(act)).build();
    }

    private PendingIntent pending(String act) {
        Intent i = new Intent(this, ControlService.class).setAction(act);
        return PendingIntent.getService(this, act.hashCode(), i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** Aus der App heraus starten oder die Adresse aktualisieren. */
    static void start(Context ctx, String url) {
        Intent i = new Intent(ctx, ControlService.class).putExtra(EXTRA_URL, url);
        try {
            ctx.startForegroundService(i);
        } catch (Exception ignored) {
            // z. B. Start aus dem Hintergrund verboten – beim nächsten Öffnen der App
        }
    }
}
