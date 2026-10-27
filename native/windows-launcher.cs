// AutoReserve native Windows entry point. Built with the Windows .NET Framework compiler.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;

// ==========================================
// Function: Present first-run progress and start only the private embedded Python process.
// Method: Use direct argument quoting, asynchronous UTF-8 pipes and a responsive native Windows message loop.
// ==========================================
internal sealed class Launcher : Form {
    private readonly string root;
    private readonly string[] arguments;
    private readonly bool quiet;
    private readonly Label status;
    private Process child;
    private StreamWriter log;
    private bool finished;
    private bool ready;
    private int result = 1;

    // ==========================================
    // Function: Construct a close-only native startup window before launching initialization.
    // Method: Delay its visibility so a verified ordinary launch does not flash a setup dialog.
    // ==========================================
    private Launcher(string folder, string[] args) {
        root = folder; arguments = args;
        quiet = args.Length > 0 && (args[0] == "--self-test" || args[0] == "--version");
        Text = "AutoReserve";
        ClientSize = new Size(520, 150);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MinimizeBox = false; MaximizeBox = false; ControlBox = false;
        ShowInTaskbar = false; Opacity = 0;
        Font = new Font("Microsoft YaHei UI", 10);
        status = new Label { Left = 24, Top = 24, Width = 472, Height = 65,
                             Text = "正在检查程序目录内的运行环境…" };
        Controls.Add(status);
        Controls.Add(new ProgressBar { Left = 24, Top = 108, Width = 472, Height = 16,
                                      Style = ProgressBarStyle.Marquee });
        Shown += delegate { StartChild(); };
        FormClosing += delegate(object sender, FormClosingEventArgs closing) {
            if (!finished) closing.Cancel = true;
        };
        var timer = new Timer { Interval = 900 };
        timer.Tick += delegate {
            timer.Stop(); timer.Dispose();
            if (!ready && !finished && !quiet) { Opacity = 1; ShowInTaskbar = true; }
        };
        timer.Start();
    }

    // ==========================================
    // Function: Preserve every filename and argument without using cmd.exe or PowerShell at runtime.
    // Method: Apply the Windows CommandLineToArgvW-compatible backslash and quote escaping rules.
    // ==========================================
    private static string Quote(string value) {
        var text = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value) {
            if (character == '\\') { slashes++; continue; }
            if (character == '"') text.Append('\\', slashes * 2 + 1);
            else text.Append('\\', slashes);
            text.Append(character); slashes = 0;
        }
        text.Append('\\', slashes * 2); text.Append('"');
        return text.ToString();
    }

    // ==========================================
    // Function: Schedule native UI updates safely from stdout and process-exit callbacks.
    // Method: Ignore messages arriving after the form is disposed and never touch controls from worker threads.
    // ==========================================
    private void Dispatch(Action action) {
        if (IsDisposed) return;
        try { BeginInvoke(action); } catch (InvalidOperationException) { }
    }

    // ==========================================
    // Function: Keep a readable launch log while displaying only initialization stages.
    // Method: Serialize UTF-8 writes and hide the temporary form once the application is ready.
    // ==========================================
    private void Output(object sender, DataReceivedEventArgs data) {
        if (data.Data == null) return;
        lock (log) { log.WriteLine(data.Data); log.Flush(); }
        Dispatch(delegate {
            if (data.Data == "AUTORESERVE_READY") { ready = true; Hide(); }
            else if (data.Data.StartsWith("SETUP:")) status.Text = data.Data.Substring(6);
        });
    }

    // ==========================================
    // Function: Launch Python and initialization from the portable folder with no global Python search.
    // Method: Redirect both pipes, inherit a cleaned child environment and wait asynchronously for completion.
    // ==========================================
    private void StartChild() {
        try {
            string logs = Path.Combine(root, ".runtime", "logs");
            Directory.CreateDirectory(logs);
            log = new StreamWriter(Path.Combine(logs, "windows-launch.log"), true, new UTF8Encoding(false));
            log.WriteLine("Launch " + DateTime.Now.ToString("O")); log.Flush();
            var command = new StringBuilder("-I -B -X utf8 -u ");
            command.Append(Quote(Path.Combine(root, "scripts", "windows_launch.py")));
            foreach (string argument in arguments) command.Append(" ").Append(Quote(argument));
            var info = new ProcessStartInfo {
                FileName = Path.Combine(root, ".runtime", "python", "python.exe"),
                Arguments = command.ToString(), WorkingDirectory = root,
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            info.EnvironmentVariables.Remove("PYTHONHOME");
            info.EnvironmentVariables.Remove("PYTHONPATH");
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            child = new Process { StartInfo = info, EnableRaisingEvents = true };
            child.OutputDataReceived += Output; child.ErrorDataReceived += Output;
            child.Exited += delegate {
                child.WaitForExit();
                Dispatch(delegate { Finish(child.ExitCode, null); });
            };
            child.Start(); child.BeginOutputReadLine(); child.BeginErrorReadLine();
        } catch (Exception error) { Finish(1, error.Message); }
    }

    // ==========================================
    // Function: Return the Python exit code and explain startup failures to mouse users.
    // Method: Keep self-tests noninteractive and point ordinary failures at the application-local log.
    // ==========================================
    private void Finish(int code, string detail) {
        finished = true; result = code;
        if (code != 0 && !quiet) MessageBox.Show(
            "启动失败。请保留解压后的完整程序文件夹。\n\n" + (detail ?? "") +
            "\n详细记录：.runtime/logs/windows-launch.log", "AutoReserve", MessageBoxButtons.OK, MessageBoxIcon.Error);
        Close();
    }

    // ==========================================
    // Function: Start the Windows GUI without a console and without requiring administrator privileges.
    // Method: Resolve the executable's own folder and return the actual child result after its native message loop ends.
    // ==========================================
    [STAThread]
    private static int Main(string[] args) {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        using (var app = new Launcher(AppDomain.CurrentDomain.BaseDirectory, args)) {
            Application.Run(app);
            if (app.log != null) app.log.Dispose();
            if (app.child != null) app.child.Dispose();
            return app.result;
        }
    }
}
