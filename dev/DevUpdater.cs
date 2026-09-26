using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Windows.Forms;

namespace JevChatDevUpdater
{
    internal sealed class UpdaterForm : Form
    {
        private static readonly string[] RepoZips = new string[]
        {
            "https://codeload.github.com/zgt47/jev-chat-windows/zip/refs/heads/dev-external-source",
            "https://github.com/zgt47/jev-chat-windows/archive/refs/heads/dev-external-source.zip"
        };

        private readonly Label statusLabel;
        private readonly TextBox detailBox;
        private readonly ProgressBar progress;
        private readonly Button retryButton;
        private readonly Button closeButton;
        private readonly BackgroundWorker worker;
        private readonly string root;

        private sealed class TimeoutWebClient : WebClient
        {
            public int TimeoutMs { get; set; }

            public TimeoutWebClient(int timeoutMs)
            {
                TimeoutMs = timeoutMs;
            }

            protected override WebRequest GetWebRequest(Uri address)
            {
                WebRequest request = base.GetWebRequest(address);
                request.Timeout = TimeoutMs;

                HttpWebRequest http = request as HttpWebRequest;
                if (http != null)
                {
                    http.ReadWriteTimeout = TimeoutMs;
                    http.AutomaticDecompression =
                        DecompressionMethods.GZip | DecompressionMethods.Deflate;
                }

                return request;
            }
        }

        public UpdaterForm()
        {
            root = AppDomain.CurrentDomain.BaseDirectory;

            Text = "Jev 开发源码更新";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = true;
            Width = 560;
            Height = 350;
            Font = new Font("Microsoft YaHei UI", 9F);

            Label titleLabel = new Label();
            titleLabel.Text = "JevChat-Windows 开发源码更新";
            titleLabel.Font = new Font(Font.FontFamily, 14F, FontStyle.Bold);
            titleLabel.AutoSize = true;
            titleLabel.Left = 24;
            titleLabel.Top = 22;
            Controls.Add(titleLabel);

            statusLabel = new Label();
            statusLabel.Text = "准备更新…";
            statusLabel.Left = 24;
            statusLabel.Top = 62;
            statusLabel.Width = 500;
            statusLabel.Height = 26;
            Controls.Add(statusLabel);

            progress = new ProgressBar();
            progress.Left = 24;
            progress.Top = 92;
            progress.Width = 500;
            progress.Height = 20;
            progress.Style = ProgressBarStyle.Marquee;
            Controls.Add(progress);

            detailBox = new TextBox();
            detailBox.Left = 24;
            detailBox.Top = 126;
            detailBox.Width = 500;
            detailBox.Height = 120;
            detailBox.Multiline = true;
            detailBox.ReadOnly = true;
            detailBox.ScrollBars = ScrollBars.Vertical;
            detailBox.Text = "强制整包同步 main.py / app / core，并清理旧 Python 缓存。\r\n不会修改 _internal、config.json、chat_profiles.json、knowledge.json、persona_skills.json、chat_history.json。";
            Controls.Add(detailBox);

            retryButton = new Button();
            retryButton.Text = "重新尝试";
            retryButton.Width = 100;
            retryButton.Height = 32;
            retryButton.Left = 318;
            retryButton.Top = 265;
            retryButton.Enabled = false;
            retryButton.Click += delegate { StartUpdate(); };
            Controls.Add(retryButton);

            closeButton = new Button();
            closeButton.Text = "关闭";
            closeButton.Width = 100;
            closeButton.Height = 32;
            closeButton.Left = 424;
            closeButton.Top = 265;
            closeButton.Click += delegate { Close(); };
            Controls.Add(closeButton);

            worker = new BackgroundWorker();
            worker.WorkerReportsProgress = true;
            worker.DoWork += WorkerDoWork;
            worker.ProgressChanged += WorkerProgressChanged;
            worker.RunWorkerCompleted += WorkerCompleted;

            Shown += delegate { BeginInvoke((MethodInvoker)StartUpdate); };
        }

        private void StartUpdate()
        {
            if (worker.IsBusy)
                return;

            Process[] running = Process.GetProcessesByName("JevChat-Dev");
            if (running.Length > 0)
            {
                statusLabel.Text = "请先关闭 JevChat-Dev.exe";
                detailBox.Text = "检测到 JevChat-Dev 仍在运行。\r\n请先关闭程序，再点击“重新尝试”。";
                progress.Style = ProgressBarStyle.Blocks;
                progress.Value = 0;
                retryButton.Enabled = true;
                return;
            }

            retryButton.Enabled = false;
            progress.Style = ProgressBarStyle.Marquee;
            statusLabel.Text = "正在开始更新…";
            detailBox.Text = "强制整包同步 main.py / app / core，并清理旧 Python 缓存。\r\n不会修改 _internal、config.json、chat_profiles.json、knowledge.json、persona_skills.json、chat_history.json。";
            worker.RunWorkerAsync();
        }

        private void WorkerDoWork(object sender, DoWorkEventArgs e)
        {
            string temp = Path.Combine(Path.GetTempPath(), "jev-chat-dev-update-" + Guid.NewGuid().ToString("N"));
            string zip = Path.Combine(temp, "src.zip");
            string extract = Path.Combine(temp, "src");
            string backup = Path.Combine(temp, "backup");

            try
            {
                Directory.CreateDirectory(temp);

                ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;

                IWebProxy systemProxy = WebRequest.DefaultWebProxy;
                string proxyText = "未检测到系统代理，使用直连";
                if (systemProxy != null)
                {
                    try
                    {
                        Uri probe = new Uri(RepoZips[0]);
                        Uri via = systemProxy.GetProxy(probe);
                        if (via != null && via != probe)
                            proxyText = "检测到 Windows 系统代理，将自动使用";
                    }
                    catch { }
                }
                Report(proxyText);

                Exception lastDownloadError = null;
                bool downloaded = false;

                for (int i = 0; i < RepoZips.Length; i++)
                {
                    string sourceName = i == 0 ? "GitHub codeload" : "GitHub 普通下载";
                    Report("正在尝试：" + sourceName + "…");

                    try
                    {
                        if (File.Exists(zip))
                            File.Delete(zip);

                        using (TimeoutWebClient client = new TimeoutWebClient(15000))
                        {
                            client.Proxy = systemProxy;
                            if (client.Proxy != null)
                                client.Proxy.Credentials = CredentialCache.DefaultCredentials;

                            client.Headers[HttpRequestHeader.UserAgent] = "JevChat-Dev-Updater";
                            client.DownloadFile(RepoZips[i], zip);
                        }

                        FileInfo zipInfo = new FileInfo(zip);
                        if (!zipInfo.Exists || zipInfo.Length < 1024)
                            throw new InvalidOperationException("下载结果为空或无效");

                        downloaded = true;
                        Report("下载完成：" + sourceName);
                        break;
                    }
                    catch (Exception ex)
                    {
                        lastDownloadError = ex;
                        Report(sourceName + " 超时或失败，自动切换下一条线路…");
                    }
                }

                if (!downloaded)
                {
                    throw new InvalidOperationException(
                        "GitHub 下载线路都不可用。请稍后重试；如果你使用代理，请确认 Windows 系统代理已开启。\r\n" +
                        "最后错误：" + (lastDownloadError == null ? "未知错误" : lastDownloadError.Message)
                    );
                }

                Report("正在解压源码…");
                Directory.CreateDirectory(extract);
                ZipFile.ExtractToDirectory(zip, extract);

                string[] roots = Directory.GetDirectories(extract);
                if (roots.Length == 0)
                    throw new InvalidOperationException("下载包结构异常：没有找到源码目录");

                string source = roots[0];
                string sourceApp = Path.Combine(source, "app");
                string sourceCore = Path.Combine(source, "core");
                string sourceMain = Path.Combine(source, "main.py");

                if (!Directory.Exists(sourceApp))
                    throw new InvalidOperationException("下载包缺少 app 目录");
                if (!Directory.Exists(sourceCore))
                    throw new InvalidOperationException("下载包缺少 core 目录");
                if (!File.Exists(sourceMain))
                    throw new InvalidOperationException("下载包缺少 main.py");

                Report("正在备份当前源码…");
                Directory.CreateDirectory(backup);
                BackupDirectoryIfExists(Path.Combine(root, "app"), Path.Combine(backup, "app"));
                BackupDirectoryIfExists(Path.Combine(root, "core"), Path.Combine(backup, "core"));
                if (File.Exists(Path.Combine(root, "main.py")))
                    File.Copy(Path.Combine(root, "main.py"), Path.Combine(backup, "main.py"), true);

                try
                {
                    Report("正在清理旧源码缓存…");
                    DeleteDirectoryIfExists(Path.Combine(root, "__pycache__"));

                    Report("正在整包替换 app / core / main.py…");
                    ReplaceDirectory(sourceApp, Path.Combine(root, "app"));
                    ReplaceDirectory(sourceCore, Path.Combine(root, "core"));
                    File.Copy(sourceMain, Path.Combine(root, "main.py"), true);

                    // 防止“旧主程序 + 新服务层”或“新主程序 + 旧服务层”混跑。
                    string analysisService = Path.Combine(root, "app", "services", "analysis_service.py");
                    string editGuard = Path.Combine(root, "app", "services", "edit_guard.py");
                    if (!File.Exists(analysisService) || !File.Exists(editGuard))
                        throw new InvalidOperationException("源码同步不完整：app/services 缺少当前版本模块");

                    string guide = Path.Combine(source, "DEV使用说明.md");
                    if (File.Exists(guide))
                        File.Copy(guide, Path.Combine(root, "DEV使用说明.md"), true);
                }
                catch
                {
                    Report("更新失败，正在恢复原源码…");
                    RestoreBackup(backup);
                    throw;
                }

                e.Result = null;
            }
            catch (Exception ex)
            {
                e.Result = ex;
            }
            finally
            {
                try
                {
                    if (Directory.Exists(temp))
                        Directory.Delete(temp, true);
                }
                catch { }
            }
        }

        private void Report(string text)
        {
            worker.ReportProgress(0, text);
        }

        private void WorkerProgressChanged(object sender, ProgressChangedEventArgs e)
        {
            string text = e.UserState as string;
            if (!String.IsNullOrEmpty(text))
            {
                statusLabel.Text = text;
                detailBox.AppendText("\r\n" + text);
            }
        }

        private void WorkerCompleted(object sender, RunWorkerCompletedEventArgs e)
        {
            Exception ex = e.Result as Exception;
            progress.Style = ProgressBarStyle.Blocks;

            if (ex == null)
            {
                progress.Value = 100;
                statusLabel.Text = "更新成功";
                detailBox.AppendText("\r\n\r\n更新完成。现在可以重新打开 JevChat-Dev.exe。");
                retryButton.Enabled = true;
                retryButton.Text = "再次更新";
                MessageBox.Show(this, "开发源码已经更新完成。", "Jev", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            else
            {
                progress.Value = 0;
                statusLabel.Text = "更新失败";
                detailBox.AppendText("\r\n\r\n失败原因：\r\n" + ex.Message);
                retryButton.Enabled = true;
                MessageBox.Show(this, ex.Message, "更新失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private static void BackupDirectoryIfExists(string source, string destination)
        {
            if (Directory.Exists(source))
                CopyDirectory(source, destination);
        }

        private void RestoreBackup(string backup)
        {
            string appBackup = Path.Combine(backup, "app");
            string coreBackup = Path.Combine(backup, "core");
            string mainBackup = Path.Combine(backup, "main.py");

            if (Directory.Exists(appBackup))
                ReplaceDirectory(appBackup, Path.Combine(root, "app"));
            if (Directory.Exists(coreBackup))
                ReplaceDirectory(coreBackup, Path.Combine(root, "core"));
            if (File.Exists(mainBackup))
                File.Copy(mainBackup, Path.Combine(root, "main.py"), true);
        }

        private static void DeleteDirectoryIfExists(string directory)
        {
            if (!Directory.Exists(directory))
                return;
            ClearReadOnly(directory);
            Directory.Delete(directory, true);
        }

        private static void ReplaceDirectory(string source, string destination)
        {
            if (Directory.Exists(destination))
            {
                ClearReadOnly(destination);
                Directory.Delete(destination, true);
            }
            CopyDirectory(source, destination);
        }

        private static void CopyDirectory(string source, string destination)
        {
            Directory.CreateDirectory(destination);

            foreach (string file in Directory.GetFiles(source))
            {
                string target = Path.Combine(destination, Path.GetFileName(file));
                File.Copy(file, target, true);
            }

            foreach (string dir in Directory.GetDirectories(source))
            {
                string target = Path.Combine(destination, Path.GetFileName(dir));
                CopyDirectory(dir, target);
            }
        }

        private static void ClearReadOnly(string directory)
        {
            foreach (string file in Directory.GetFiles(directory, "*", SearchOption.AllDirectories))
            {
                try
                {
                    FileAttributes attrs = File.GetAttributes(file);
                    if ((attrs & FileAttributes.ReadOnly) != 0)
                        File.SetAttributes(file, attrs & ~FileAttributes.ReadOnly);
                }
                catch { }
            }
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new UpdaterForm());
        }
    }
}
