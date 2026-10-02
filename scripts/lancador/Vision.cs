// Vision.exe: o lançador do Vision. Um executável pequeno, com o ícone do orbe, que liga o assistente a partir da
// pasta do projeto (`.venv\Scripts\visionw.exe`) e abre a janela. Se o Vision já estiver rodando, só abre a janela
// (quem decide isso é o próprio núcleo, que tem instância única).
//
// Compilado por scripts/gerar_exe.py com o csc.exe que vem no Windows (.NET Framework 4): nada para instalar.
// A pasta do projeto é a do próprio exe (ou até duas acima, para quem o põe numa subpasta) ou a da variável
// VISION_HOME. Só vale pasta que tenha o `pyproject.toml` do projeto junto do `.venv` (revisão do PR 36).

using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Vision")]
[assembly: AssemblyProduct("Vision")]
[assembly: AssemblyDescription("Assistente de voz local")]
[assembly: AssemblyVersion("1.0.0.0")]

static class Lancador
{
    const string Alvo = @".venv\Scripts\visionw.exe";

    static bool EhProjeto(string pasta)
    {
        return File.Exists(Path.Combine(pasta, Alvo)) && File.Exists(Path.Combine(pasta, "pyproject.toml"));
    }

    static string AcharProjeto()
    {
        string casa = Environment.GetEnvironmentVariable("VISION_HOME");
        if (!string.IsNullOrEmpty(casa) && EhProjeto(casa)) return casa;
        string pasta = AppDomain.CurrentDomain.BaseDirectory;
        for (int i = 0; i < 3 && pasta != null; i++)
        {
            if (EhProjeto(pasta)) return pasta;
            pasta = Path.GetDirectoryName(pasta.TrimEnd('\\'));
        }
        return null;
    }

    // Um argumento na linha de comando do Windows (regra do CommandLineToArgvW): com espaço, tab ou aspas, vai entre
    // aspas, e as barras invertidas antes de aspas dobram. Sem isso, "uma frase" virava dois argumentos.
    static string Escapar(string arg)
    {
        if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0) return arg;
        var sb = new StringBuilder("\"");
        for (int i = 0; i < arg.Length; i++)
        {
            int barras = 0;
            while (i < arg.Length && arg[i] == '\\') { barras++; i++; }
            if (i == arg.Length) { sb.Append('\\', barras * 2); break; }
            if (arg[i] == '"') sb.Append('\\', barras * 2 + 1).Append('"');
            else sb.Append('\\', barras).Append(arg[i]);
        }
        return sb.Append('"').ToString();
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
        string argumentos = args.Length == 0 ? "--abrir" : string.Join(" ", Array.ConvertAll(args, Escapar));
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
                if (p == null) return 0;
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
            // Mensagem fixa: a do sistema traz o caminho completo (e com ele o nome de usuário do Windows).
            int codigo = e is System.ComponentModel.Win32Exception ? ((System.ComponentModel.Win32Exception)e).NativeErrorCode : 0;
            Erro("Não consegui ligar o Vision (erro " + codigo + "). Veja o arquivo data\\logs\\nucleo.log.");
            return 1;
        }
        return 0;
    }
}
