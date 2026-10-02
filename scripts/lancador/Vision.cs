// Vision.exe: o lançador do Vision. Um executável pequeno, com o ícone do orbe, que liga o assistente a partir da
// pasta do projeto (`.venv\Scripts\visionw.exe`) e abre a janela. Se o Vision já estiver rodando, só abre a janela
// (quem decide isso é o próprio núcleo, que tem instância única).
//
// Compilado por scripts/gerar_exe.py com o csc.exe que vem no Windows (.NET Framework 4): nada para instalar.
// A pasta do projeto é a do próprio exe (ou uma acima, para quem o põe em dist\), ou a da variável VISION_HOME.

using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Vision")]
[assembly: AssemblyProduct("Vision")]
[assembly: AssemblyDescription("Assistente de voz local")]
[assembly: AssemblyVersion("1.0.0.0")]

static class Lancador
{
    const string Alvo = @".venv\Scripts\visionw.exe";

    static string AcharProjeto()
    {
        string casa = Environment.GetEnvironmentVariable("VISION_HOME");
        if (!string.IsNullOrEmpty(casa) && File.Exists(Path.Combine(casa, Alvo))) return casa;
        string pasta = AppDomain.CurrentDomain.BaseDirectory;
        for (int i = 0; i < 3 && pasta != null; i++)
        {
            if (File.Exists(Path.Combine(pasta, Alvo))) return pasta;
            pasta = Path.GetDirectoryName(pasta.TrimEnd('\\'));
        }
        return null;
    }

    static void Erro(string texto)
    {
        MessageBox.Show(texto, "Vision", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    [STAThread]
    static int Main(string[] args)
    {
        string projeto = AcharProjeto();
        if (projeto == null)
        {
            Erro("Não achei o Vision. Ponha o Vision.exe dentro da pasta do projeto (a que tem a pasta .venv) " +
                 "ou defina a variável VISION_HOME com o caminho dela.");
            return 2;
        }
        // Sem argumentos, abre a janela. Com argumentos (ex.: --sem-voz), repassa como estão.
        string argumentos = args.Length == 0 ? "--abrir" : string.Join(" ", args);
        var info = new ProcessStartInfo
        {
            FileName = Path.Combine(projeto, Alvo),
            Arguments = argumentos,
            WorkingDirectory = projeto,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        try
        {
            using (Process p = Process.Start(info))
            {
                // O núcleo novo fica rodando; um que sai logo com erro é falha de partida.
                if (p.WaitForExit(6000) && p.ExitCode != 0)
                {
                    Erro("O Vision não iniciou (código " + p.ExitCode + "). Veja o arquivo data\\logs\\nucleo.log.");
                    return p.ExitCode;
                }
            }
        }
        catch (Exception e)
        {
            Erro("Não consegui ligar o Vision: " + e.Message);
            return 1;
        }
        return 0;
    }
}
