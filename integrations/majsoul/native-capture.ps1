param([ValidatePattern('^[0-9a-fA-F]*$')][string]$Handle = '', [ValidateRange(160000,2304000)][int]$PixelBudget = 640000)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
public class MahjongWindow {
  public string handle, title; public int pid; public bool minimized;
}
public static class MahjongCapture {
  delegate bool EnumProc(IntPtr h, IntPtr p);
  [StructLayout(LayoutKind.Sequential)] struct Rect { public int left, top, right, bottom; }
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out int pid);
  [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr h, out Rect r);
  [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr c);
  public static MahjongWindow[] Windows() {
    SetProcessDpiAwarenessContext(new IntPtr(-4));
    var found = new List<MahjongWindow>();
    EnumWindows((h, p) => {
      if (!IsWindowVisible(h)) return true;
      var s = new StringBuilder(512); GetWindowText(h, s, s.Capacity);
      string title = s.ToString();
      if (!Regex.IsMatch(title, "\\u96c0\\u9b42|Mahjong\\s*Soul", RegexOptions.IgnoreCase)) return true;
      int pid; GetWindowThreadProcessId(h, out pid);
      try {
        string exe = Process.GetProcessById(pid).ProcessName;
        if (!Regex.IsMatch(exe, "^(Jantama_MahjongSoul|MahjongSoul|\\u96c0\\u9b42(?:\\u9ebb[\\u5c06\\u5c07])?)$", RegexOptions.IgnoreCase)) return true;
      } catch { return true; }
      found.Add(new MahjongWindow {handle=h.ToInt64().ToString("x"),title=title,pid=pid,minimized=IsIconic(h)});
      return true;
    }, IntPtr.Zero);
    return found.ToArray();
  }
  static bool HasPicture(Bitmap b) {
    int low=255, high=0;
    for (int y=1;y<16;y++) for (int x=1;x<16;x++) {
      var c=b.GetPixel(x*b.Width/16,y*b.Height/16); int v=(c.R+c.G+c.B)/3;
      low=Math.Min(low,v); high=Math.Max(high,v);
    }
    return high-low>16;
  }
  public static int OutputWidth, OutputHeight;
  public static byte[] Gray;
  public static byte[] Capture(MahjongWindow target, int pixelBudget) {
    IntPtr h=new IntPtr(Convert.ToInt64(target.handle,16));
    if (IsIconic(h)) throw new Exception("WINDOW_MINIMIZED");
    Rect r; if (!GetClientRect(h,out r)) throw new Exception("WINDOW_CLOSED");
    int w=r.right-r.left, height=r.bottom-r.top;
    if (w<200 || height<150 || w>10000 || height>6000) throw new Exception("WINDOW_SIZE");
    using (var b=new Bitmap(w,height,PixelFormat.Format32bppArgb)) {
      bool printed;
      using (var g=Graphics.FromImage(b)) {
        var dc=g.GetHdc(); try { printed=PrintWindow(h,dc,3); } finally { g.ReleaseHdc(dc); }
      }
      // Never fall back to desktop capture: overlapping windows are outside consent.
      if (!printed || !HasPicture(b)) throw new Exception("WINDOW_BLANK");
      int owner; GetWindowThreadProcessId(h,out owner);
      var title=new StringBuilder(512); GetWindowText(h,title,title.Capacity);
      if (owner!=target.pid || title.ToString()!=target.title || IsIconic(h)) throw new Exception("WINDOW_CHANGED");
      double scale=Math.Min(1,Math.Min(Math.Sqrt((double)pixelBudget/(w*height)),Math.Min(1920.0/w,1200.0/height)));
      OutputWidth=Math.Max(1,(int)(w*scale)); OutputHeight=Math.Max(1,(int)(height*scale));
      // Fingerprint the full game window locally. It is never attached to model messages.
      using (var small=new Bitmap(b,new Size(128,72))) {
        Gray=new byte[128*72];
        for (int y=0;y<72;y++) for (int x=0;x<128;x++) {
          var p=small.GetPixel(x,y); Gray[y*128+x]=(byte)((p.R*299+p.G*587+p.B*114)/1000);
        }
      }
      using (var output=new Bitmap(b,new Size((int)(w*scale),(int)(height*scale))))
      using (var stream=new MemoryStream()) {
        ImageCodecInfo codec=null;
        foreach (var c in ImageCodecInfo.GetImageEncoders()) if (c.MimeType=="image/jpeg") codec=c;
        using (var options=new EncoderParameters(1)) {
          options.Param[0]=new EncoderParameter(System.Drawing.Imaging.Encoder.Quality,85L);
          output.Save(stream,codec,options);
        }
        return stream.ToArray();
      }
    }
  }
}
'@
try {
    $windows = @([MahjongCapture]::Windows())
    $matches = @($windows | Where-Object { -not $Handle -or $_.handle -eq $Handle })
    if ($matches.Count -eq 0) { throw 'WINDOW_NOT_FOUND' }
    if ($matches.Count -gt 1) { throw 'WINDOW_AMBIGUOUS' }
    $target = $matches[0]
    $bytes = [MahjongCapture]::Capture($target, $PixelBudget)
    @{ ok=$true; window=$target; jpeg=[Convert]::ToBase64String($bytes); gray=[Convert]::ToBase64String([MahjongCapture]::Gray); width=[MahjongCapture]::OutputWidth; height=[MahjongCapture]::OutputHeight } | ConvertTo-Json -Compress -Depth 4
} catch {
    @{ ok=$false; error=$_.Exception.Message; windows=$windows } | ConvertTo-Json -Compress -Depth 4
}
