package de.klangkiste.app;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.net.Uri;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsetsController;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.URL;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Hülle um die Weboberfläche der Klangkiste. Sucht die Box im WLAN (mDNS-Dienst
 * „_klangkiste._tcp“, sonst bekannte Namen), merkt sich die Adresse und sucht neu,
 * wenn sie nicht mehr antwortet – etwa weil die Box eine neue IP bekommen hat.
 */
public class MainActivity extends Activity {
    private static final String SERVICE = "_klangkiste._tcp.";
    private static final String[] GUESSES = {"http://klangkiste.local", "http://klangkiste.fritz.box", "http://klangkiste"};
    private static final int FILE_REQUEST = 1;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private WebView web;
    private LinearLayout panel;
    private TextView panelText;
    private ProgressBar spinner;
    private EditText address;
    private Button retry;
    private ValueCallback<Uri[]> fileCallback;
    private NsdManager nsd;
    private NsdManager.DiscoveryListener discovery;
    private WifiManager.MulticastLock multicast;
    private int search = 0;   // laufende Suche; ältere Ergebnisse werden verworfen

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("klangkiste", MODE_PRIVATE);
        nsd = (NsdManager) getSystemService(Context.NSD_SERVICE);
        buildUi();
        lightBars();
        if (state != null) {
            web.restoreState(state);
        }
        String saved = prefs.getString("url", null);
        if (state == null) {
            if (saved != null) load(saved); else find(false);
        }
    }

    // ---------- Oberfläche ----------
    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUserAgentString(s.getUserAgentString() + " KlangkisteApp/1");
        CookieManager.getInstance().setAcceptCookie(true);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if ("klangkiste".equals(u.getScheme())) {   // Knopf „Adresse ändern“ in den Einstellungen
                    showPanel("Adresse der Klangkiste", false);
                    return true;
                }
                String base = prefs.getString("url", "");
                if (!u.toString().startsWith(base)) {     // fremde Seiten im Browser öffnen
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                    return true;
                }
                return false;
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) {
                    v.loadUrl("about:blank");
                    find(true);
                }
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                if (url.startsWith("http")) panel.setVisibility(View.GONE);
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;
                try {
                    Intent i = p.createIntent();
                    if (p.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                    startActivityForResult(Intent.createChooser(i, "Datei wählen"), FILE_REQUEST);
                } catch (Exception ex) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
        web.setDownloadListener((url, ua, disposition, mime, length) -> {
            try {
                String name = URLUtil.guessFileName(url, disposition, mime);
                DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url))
                        .setMimeType(mime)
                        .setTitle(name)
                        .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                        .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(req);
                Toast.makeText(this, "Download gestartet: " + name, Toast.LENGTH_SHORT).show();
            } catch (Exception ex) {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        int pad = dp(28);
        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        panel.setPadding(pad, pad, pad, pad);
        panel.setBackgroundColor(night() ? 0xFF12211F : 0xFFFBF6EE);
        int ink = night() ? 0xFFF1EBE0 : 0xFF16323A;

        TextView title = new TextView(this);
        title.setText("Klangkiste");
        title.setTextSize(28);
        title.setTextColor(ink);
        title.setGravity(Gravity.CENTER);
        panel.addView(title);

        spinner = new ProgressBar(this);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(dp(40), dp(40));
        sp.topMargin = dp(24);
        panel.addView(spinner, sp);

        panelText = new TextView(this);
        panelText.setTextSize(16);
        panelText.setTextColor(ink);
        panelText.setGravity(Gravity.CENTER);
        panelText.setPadding(0, dp(16), 0, dp(16));
        panel.addView(panelText);

        address = new EditText(this);
        address.setHint("z. B. 192.168.178.63");
        address.setSingleLine(true);
        address.setTextColor(ink);
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setImeOptions(EditorInfo.IME_ACTION_GO);
        address.setOnEditorActionListener((v, id, ev) -> { useTyped(); return true; });
        panel.addView(address, new LinearLayout.LayoutParams(-1, -2));

        Button go = new Button(this);
        go.setText("Verbinden");
        go.setOnClickListener(v -> useTyped());
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(-1, -2);
        gp.topMargin = dp(8);
        panel.addView(go, gp);

        retry = new Button(this);
        retry.setText("Automatisch suchen");
        retry.setOnClickListener(v -> find(false));
        panel.addView(retry, new LinearLayout.LayoutParams(-1, -2));

        TextView hint = new TextView(this);
        hint.setText("Handy und Klangkiste müssen im selben WLAN sein. Die Adresse sagt die Box an, wenn du Play/Pause drei Sekunden gedrückt hältst.");
        hint.setTextSize(13);
        hint.setTextColor(night() ? 0xFF9DB3B0 : 0xFF5D7278);
        hint.setGravity(Gravity.CENTER);
        hint.setPadding(0, dp(18), 0, 0);
        panel.addView(hint);

        root.addView(panel, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
    }

    private void showPanel(String text, boolean searching) {
        panel.setVisibility(View.VISIBLE);
        panelText.setText(text);
        spinner.setVisibility(searching ? View.VISIBLE : View.GONE);
        address.setVisibility(searching ? View.GONE : View.VISIBLE);
        retry.setVisibility(searching ? View.GONE : View.VISIBLE);
        String saved = prefs.getString("url", "");
        if (!searching && address.getText().length() == 0 && !saved.isEmpty()) {
            address.setText(saved.replaceFirst("^http://", ""));
        }
        // „Verbinden“ (Kind direkt nach dem Eingabefeld) nur zusammen mit dem Feld zeigen
        View go = panel.getChildAt(panel.indexOfChild(address) + 1);
        go.setVisibility(searching ? View.GONE : View.VISIBLE);
    }

    private void useTyped() {
        String a = address.getText().toString().trim();
        if (a.isEmpty()) return;
        if (!a.startsWith("http://") && !a.startsWith("https://")) a = "http://" + a;
        a = a.replaceAll("/+$", "");
        final String url = a;
        showPanel("Verbinde mit " + url.replaceFirst("^https?://", "") + " …", true);
        new Thread(() -> {
            boolean ok = reachable(url);
            ui.post(() -> {
                if (ok) { save(url); load(url); }
                else showPanel("Unter dieser Adresse antwortet keine Klangkiste.", false);
            });
        }).start();
    }

    // ---------- Box finden ----------
    private void find(boolean lost) {
        final int my = ++search;
        showPanel(lost ? "Verbindung verloren – suche die Klangkiste …" : "Suche die Klangkiste im WLAN …", true);
        final AtomicBoolean done = new AtomicBoolean(false);
        final String saved = prefs.getString("url", null);

        // 1. Gemerkte Adresse und bekannte Namen nebenbei ausprobieren
        new Thread(() -> {
            if (saved != null && reachable(saved)) { found(my, done, saved); return; }
            for (String g : GUESSES) {
                if (done.get()) return;
                if (reachable(g)) { found(my, done, g); return; }
            }
        }).start();

        // 2. mDNS: die Box meldet sich als „_klangkiste._tcp“
        stopDiscovery();
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
            multicast = wifi.createMulticastLock("klangkiste");
            multicast.setReferenceCounted(false);
            multicast.acquire();
        } catch (Exception ignored) { }
        discovery = new NsdManager.DiscoveryListener() {
            @Override public void onDiscoveryStarted(String t) { }
            @Override public void onDiscoveryStopped(String t) { }
            @Override public void onStartDiscoveryFailed(String t, int c) { }
            @Override public void onStopDiscoveryFailed(String t, int c) { }
            @Override public void onServiceLost(NsdServiceInfo i) { }
            @Override
            public void onServiceFound(NsdServiceInfo info) {
                nsd.resolveService(info, new NsdManager.ResolveListener() {
                    @Override public void onResolveFailed(NsdServiceInfo i, int c) { }
                    @Override
                    public void onServiceResolved(NsdServiceInfo i) {
                        InetAddress h = i.getHost();
                        if (h == null) return;
                        String host = h.getHostAddress();
                        if (host.contains(":")) return;   // nur IPv4 – die Box lauscht nur dort
                        String url = "http://" + host + (i.getPort() == 80 ? "" : ":" + i.getPort());
                        new Thread(() -> { if (reachable(url)) found(my, done, url); }).start();
                    }
                });
            }
        };
        try {
            nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, discovery);
        } catch (Exception e) {
            discovery = null;
        }

        // 3. Nach 12 s aufgeben und nach der Adresse fragen
        ui.postDelayed(() -> {
            if (my == search && !done.get()) {
                stopDiscovery();
                showPanel("Keine Klangkiste gefunden. Gib ihre Adresse ein:", false);
            }
        }, 12000);
    }

    private void found(int my, AtomicBoolean done, String url) {
        if (!done.compareAndSet(false, true)) return;
        ui.post(() -> {
            if (my != search) return;
            stopDiscovery();
            save(url);
            load(url);
        });
    }

    private void stopDiscovery() {
        if (discovery != null) {
            try { nsd.stopServiceDiscovery(discovery); } catch (Exception ignored) { }
            discovery = null;
        }
        if (multicast != null && multicast.isHeld()) multicast.release();
    }

    private static boolean reachable(String base) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/state").openConnection();
            c.setConnectTimeout(2500);
            c.setReadTimeout(2500);
            int code = c.getResponseCode();
            c.disconnect();
            return code == 200;
        } catch (Exception e) {
            return false;
        }
    }

    private void save(String url) { prefs.edit().putString("url", url).apply(); }

    /** Steuerung in Sperrbildschirm und Benachrichtigungsleiste starten. */
    private void startControls(String url) {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                && !prefs.getBoolean("asked_notify", false)) {
            prefs.edit().putBoolean("asked_notify", true).apply();
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 2);
        }
        ControlService.start(this, url);
    }

    private void load(String url) {
        showPanel("Verbinde …", true);
        web.loadUrl(url + "/");
        startControls(url);
    }

    // ---------- Android-Kleinkram ----------
    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == FILE_REQUEST && fileCallback != null) {
            fileCallback.onReceiveValue(multi(res, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(req, res, data);
    }

    private static Uri[] multi(int res, Intent data) {
        if (res != RESULT_OK || data == null) return null;
        if (data.getClipData() != null) {
            Uri[] out = new Uri[data.getClipData().getItemCount()];
            for (int i = 0; i < out.length; i++) out[i] = data.getClipData().getItemAt(i).getUri();
            return out;
        }
        return data.getData() != null ? new Uri[]{data.getData()} : null;
    }

    @Override
    public void onBackPressed() {
        if (panel.getVisibility() == View.VISIBLE && web.getUrl() != null && web.getUrl().startsWith("http")) {
            panel.setVisibility(View.GONE);
        } else if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        // War die Steuerung wegen Pause verschwunden, kommt sie beim Öffnen der App zurück
        String url = prefs.getString("url", null);
        if (url != null && web.getUrl() != null && web.getUrl().startsWith("http")) ControlService.start(this, url);
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onDestroy() {
        stopDiscovery();
        super.onDestroy();
    }

    private boolean night() {
        return (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
    }

    private void lightBars() {
        int bg = night() ? 0xFF12211F : 0xFFFBF6EE;
        getWindow().setStatusBarColor(bg);
        getWindow().setNavigationBarColor(bg);
        if (!night()) {
            if (Build.VERSION.SDK_INT >= 30) {
                WindowInsetsController c = getWindow().getInsetsController();
                if (c != null) c.setSystemBarsAppearance(
                        WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
                        WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
            } else {
                int f = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
                if (Build.VERSION.SDK_INT >= 26) f |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
                getWindow().getDecorView().setSystemUiVisibility(f);
            }
        }
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }
}
