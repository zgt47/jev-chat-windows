using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        string root = AppContext.BaseDirectory;
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        string main = Path.Combine(root, "main.py");

        if (!File.Exists(python))
        {
            MessageBox.Show("缺少 runtime\\pythonw.exe，请重新下载开发环境完整包。", "JevChat 开发版");
            return;
        }
        if (!File.Exists(main))
        {
            MessageBox.Show("缺少 main.py，请运行“更新开发源码.cmd”或重新下载开发环境。", "JevChat 开发版");
            return;
        }

        var psi = new ProcessStartInfo
        {
            FileName = python,
            Arguments = "\"main.py\"",
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        Process.Start(psi);
    }
}
