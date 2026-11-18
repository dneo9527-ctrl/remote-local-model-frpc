using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace LocalAIControl {
 public sealed class PanelOperationLock : IDisposable {
  Mutex mutex;bool held;
  public PanelOperationLock() {
   mutex=new Mutex(false,"Local\\LocalAIControl-"+Environment.UserName);
   try{try{held=mutex.WaitOne(0);}catch(AbandonedMutexException){held=true;}if(!held)throw new IOException("服务操作正在进行，请完成后再保存设置。");}
   catch{mutex.Dispose();mutex=null;throw;}
  }
  public void Dispose(){if(mutex==null)return;try{if(held)mutex.ReleaseMutex();}finally{mutex.Dispose();mutex=null;held=false;}}
 }
 public static class PanelPreferences {
  public static Dictionary<string,object> Read(string path) {
   return ReadSnapshot(Bytes(path));
  }
  public static Dictionary<string,object> ReadSnapshot(byte[] bytes) {
   if(bytes==null)return new Dictionary<string,object>();
   var result=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(new UTF8Encoding(false,true).GetString(bytes).TrimStart('\uFEFF'));
   if(result==null)throw new InvalidDataException("面板设置无法读取，原文件已保留。");return result;
  }
  public static int Port(Dictionary<string,object> values,string key,int fallback) {
   object raw;if(!values.TryGetValue(key,out raw))return fallback;
   int port;if(!(raw is int)||!Int32.TryParse(Convert.ToString(raw),out port)||port<1||port>65535)throw new InvalidDataException(key+" 需为 1–65535 的整数。");return port;
  }
  public static string WebUrl(string value) {
   if(String.IsNullOrWhiteSpace(value))return "";
   Uri uri;if(!Uri.TryCreate(value.Trim(),UriKind.Absolute,out uri)||(uri.Scheme!="http"&&uri.Scheme!="https")||uri.Port<1||!String.IsNullOrEmpty(uri.UserInfo)||value.Any(Char.IsControl))throw new ArgumentException("网页地址需为完整 HTTP/HTTPS 地址，不能包含用户名或密码。");
   return uri.AbsoluteUri;
  }
  public static byte[] Bytes(string path){return File.Exists(path)?File.ReadAllBytes(path):null;}
  public static string Hash(byte[] bytes){if(bytes==null)return "missing";using(var sha=SHA256.Create())return Convert.ToBase64String(sha.ComputeHash(bytes));}
  public static void RecoverPorts(string root) {
   string folder=Path.Combine(root,"config"),pending=Path.Combine(folder,".panel-port-change.pending.json");if(!File.Exists(pending))return;
   var plan=Read(pending);string config=Path.Combine(folder,"frpc.toml"),prefs=Path.Combine(folder,"panel-preferences.json");
   byte[] configBytes=Bytes(config),prefsBytes=Bytes(prefs);string ch=Hash(configBytes),ph=Hash(prefsBytes);Func<string,string> expected=key=>plan.ContainsKey(key)?Convert.ToString(plan[key]):"invalid";
   if((ch==expected("configBefore")&&ph==expected("prefsBefore"))||(ch==expected("configAfter")&&ph==expected("prefsAfter"))){File.Delete(pending);return;}
   byte[] backup=Bytes(config+".port-previous.bak");
   if(ch==expected("configAfter")&&ph==expected("prefsBefore")&&Hash(backup)==expected("configBefore")){Write(config,configBytes,backup,null);File.Delete(pending);return;}
   throw new IOException("检测到未完成的端口保存与外部变化，请保留 .port-previous.bak 和 pending 记录，核对后再启动。");
  }
  public static bool Same(byte[] a,byte[] b){return a==null?b==null:b!=null&&a.SequenceEqual(b);}
  public static void Write(string path,byte[] expected,byte[] data,string backup) {
   if(!Same(Bytes(path),expected))throw new IOException("配置已被其他程序修改，请重新打开设置。");
   if(Same(expected,data))return;
   string temp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
   try {File.WriteAllBytes(temp,data);if(!Same(Bytes(path),expected))throw new IOException("配置在保存时发生变化，请重试。");if(expected==null)File.Move(temp,path);else File.Replace(temp,path,backup);}
   finally{if(File.Exists(temp))File.Delete(temp);}
  }
 }
 public sealed partial class Panel {
  bool openingWeb;
  int ModelPort {get{return PanelPreferences.Port(PanelPreferences.Read(Path.Combine(root,"config","panel-preferences.json")),"ollamaPort",11434);}}
  int VisitorPort {get{return PanelPreferences.Port(PanelPreferences.Read(Path.Combine(root,"config","panel-preferences.json")),"visitorPort",11436);}}
  string ModelUrl {get{return "http://127.0.0.1:"+ModelPort;}}
  Window SmallWindow(string title,int height=420) {
   var dialog=new Window{Title=title,Owner=Window,Width=600,Height=height,MinWidth=500,MinHeight=320,WindowStartupLocation=WindowStartupLocation.CenterOwner,Background=Brush("#F6F8FC"),FontFamily=Window.FontFamily,FontSize=13};
   dialog.Resources=Window.Resources;dialog.PreviewKeyDown+=(s,e)=>{if(e.Key==System.Windows.Input.Key.Escape&&!busy){dialog.Close();e.Handled=true;}};return dialog;
  }
  Button SmallButton(string label){return new Button{Content=label,Padding=new Thickness(14,6,14,6),MinHeight=32,Margin=new Thickness(0,0,8,0)};}
  TextBlock Paragraph(string text,bool bold=false){return new TextBlock{Text=text,TextWrapping=TextWrapping.Wrap,Foreground=Brush("#4C6280"),FontWeight=bold?FontWeights.SemiBold:FontWeights.Normal,Margin=new Thickness(0,0,0,12)};}
  void ConnectionWindow() {
   var dialog=SmallWindow("连接信息",415);var body=new StackPanel{Margin=new Thickness(24)};
   body.Children.Add(Paragraph("这台 Windows",true));body.Children.Add(Paragraph(ModelUrl+"  ·  Ollama\n"+ModelUrl+"/v1  ·  OpenAI 兼容"));
   var copy=SmallButton("复制本机 OpenAI 地址");copy.Click+=(s,e)=>{try{Clipboard.SetText(ModelUrl+"/v1");copy.Content="已复制";}catch{copy.Content="剪贴板暂不可用";}};body.Children.Add(copy);
   body.Children.Add(new Border{Height=20});body.Children.Add(Paragraph("Mac / 手机 / 其他接收设备",true));body.Children.Add(Paragraph("从线路右侧复制 visitor TOML，放到该设备的 frpc 中运行。客户端填该设备自己的回环入口；默认 http://127.0.0.1:"+VisitorPort+"，以复制配置的 bindPort 为准。\n127.0.0.1 指当前设备，不能直接当成 Windows 的公网地址。"));
   body.Children.Add(Paragraph(state==null?"默认模型尚未读取":"项目默认模型："+S(state,"Model")));dialog.Content=new ScrollViewer{Content=body,VerticalScrollBarVisibility=ScrollBarVisibility.Auto};dialog.ShowDialog();
  }
  void HistoryWindow() {
   var dialog=SmallWindow("日志与操作记录",460);var grid=new DockPanel{Margin=new Thickness(20)};var bar=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,0,0,12)};
   var folder=SmallButton("打开日志目录");folder.Click+=(s,e)=>{try{string path=Path.Combine(root,"logs");Directory.CreateDirectory(path);Process.Start("explorer.exe",Q(path));}catch(Exception ex){MessageBox.Show(dialog,ex.Message,"未能打开目录");}};
   var refresh=SmallButton("刷新记录");bar.Children.Add(folder);bar.Children.Add(refresh);DockPanel.SetDock(bar,Dock.Top);grid.Children.Add(bar);
   var text=new TextBox{IsReadOnly=true,TextWrapping=TextWrapping.Wrap,VerticalScrollBarVisibility=ScrollBarVisibility.Auto,Padding=new Thickness(12),Text=Control<TextBox>("History").Text};refresh.Click+=(s,e)=>{text.Text=Control<TextBox>("History").Text;text.ScrollToEnd();};grid.Children.Add(text);dialog.Content=grid;dialog.ShowDialog();
  }
  void ExportRoute(Route route) {
   if(busy||route==null)return;
   try {
    ValidateRoute(route);int bindPort=VisitorPort;string toml="# 线路："+String.Concat(route.Name.Select(c=>Char.IsControl(c)?' ':c))+Environment.NewLine+VisitorExport.Build(File.ReadAllText(Path.Combine(root,"config","frpc.toml"),Encoding.UTF8),route.Address,bindPort);
    Clipboard.SetText(toml,TextDataFormat.UnicodeText);
    History("已复制接收设备 visitor TOML；含私密连接密钥，请私下保管。未切换当前线路。");Banner("已复制“"+route.Name+"”的 TOML，接收端口 "+bindPort+"。含连接密钥，请私下粘贴；未切线。","#E0F3EB");
   }catch(System.Runtime.InteropServices.ExternalException){Banner("剪贴板正被其他程序占用，请稍后再点“复制配置”。未生成文件或切换线路。","#FFF0D8");}
   catch(Exception){Banner("复制未完成：当前需为可支持的单行 STCP/token 配置。复杂或动态凭据配置请在原 frpc 工具中处理。","#FFF0D8");}
  }
  async Task OpenWeb() {
   if(busy||openingWeb)return;openingWeb=true;Button("WebButton").IsEnabled=false;
   try {
    var pref=PanelPreferences.Read(Path.Combine(root,"config","panel-preferences.json"));object raw;string url=pref.TryGetValue("webChatUrl",out raw)?PanelPreferences.WebUrl(Convert.ToString(raw)):"";
    if(String.IsNullOrEmpty(url)) {
     bool shared=false;try{shared=await Task.Run(()=>{var request=(HttpWebRequest)WebRequest.Create("http://127.0.0.1:18765/api/session");request.Proxy=null;request.Timeout=1200;request.ReadWriteTimeout=1200;request.AllowAutoRedirect=false;using(var response=request.GetResponse())using(var reader=new StreamReader(response.GetResponseStream())){char[] chars=new char[8193];int count=reader.ReadBlock(chars,0,chars.Length);if(count>8192)return false;var identity=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(new string(chars,0,count));return S(identity,"service")=="local-ai-console";}});}catch{}
     if(shared)url="http://127.0.0.1:18765/chat";
     else {SettingsWindow("先填写已部署的网页聊天地址，例如本机 Open WebUI；Ollama API 本身不提供聊天网页。");return;}
    }
    Process.Start(new ProcessStartInfo(url){UseShellExecute=true});History("已请求打开配置的网页版。页面是否可用以浏览器实际连接为准。");
   }catch(Exception ex){Banner(ex.Message,"#FFF0D8");}finally{openingWeb=false;if(!closed)Button("WebButton").IsEnabled=!busy;}
  }
  void SettingsWindow(string notice="") {
   if(busy)return;
   string prefPath=Path.Combine(root,"config","panel-preferences.json"),configPath=Path.Combine(root,"config","frpc.toml");
   Dictionary<string,object> values;byte[] originalPref,originalConfig;int[] ports;int initialModel,initialVisitor;
   try{using(new PanelOperationLock()){PanelPreferences.RecoverPorts(root);originalPref=PanelPreferences.Bytes(prefPath);values=PanelPreferences.ReadSnapshot(originalPref);originalConfig=File.ReadAllBytes(configPath);ports=PortConfig.Read(new UTF8Encoding(false,true).GetString(originalConfig).TrimStart('\uFEFF'));initialModel=PanelPreferences.Port(values,"ollamaPort",11434);initialVisitor=PanelPreferences.Port(values,"visitorPort",11436);}}
   catch{Banner("设置无法读取：原配置已保留。请先检查 STCP 配置格式及面板偏好文件。","#FFF0D8");return;}
   var dialog=SmallWindow("设置 · 端口与客户端",565);dialog.ResizeMode=ResizeMode.CanResize;var content=new StackPanel{Margin=new Thickness(24)};
   content.Children.Add(Paragraph("连接与端口",true));content.Children.Add(Paragraph("端口修改会保存配置，不会偷偷重启服务。改模型/frps 端口前需停止本项目隧道；之后点击右上启动。"));
   Func<string,string,TextBox> field=(label,value)=>{content.Children.Add(new TextBlock{Text=label,Foreground=Brush("#526987"),Margin=new Thickness(0,0,0,5)});var box=new TextBox{Text=value,Padding=new Thickness(9,7,9,7),Margin=new Thickness(0,0,0,12)};content.Children.Add(box);return box;};
   var model=field("模型 API 端口（Windows 回环监听）",initialModel.ToString());
   var frps=field("frps 服务端端口（需与 VPS 上的 frps 一致）",ports[0].ToString());
   var visitor=field("接收设备本地端口（复制配置时使用）",initialVisitor.ToString());
   object current;var web=field("网页聊天地址（已部署的 Open WebUI / 共享 /chat 页面）",values.TryGetValue("webChatUrl",out current)?Convert.ToString(current):"");
   var status=Paragraph(String.IsNullOrEmpty(notice)?"网页地址和接收端口保存后立即生效。模型端口同步到本项目 frpc localPort，旧端口上的模型服务保留。":notice);content.Children.Add(status);
   var save=SmallButton("保存设置");save.Background=Brush("#3677F6");save.BorderBrush=Brush("#3677F6");save.Foreground=Brushes.White;var close=SmallButton("取消");var row=new StackPanel{Orientation=Orientation.Horizontal};row.Children.Add(save);row.Children.Add(close);content.Children.Add(row);close.Click+=(s,e)=>dialog.Close();
   save.Click+=async(s,e)=>{
    IDisposable operation=null;byte[] changed=null;bool configChanged=false,committed=false;string pending=Path.Combine(root,"config",".panel-port-change.pending.json");
    try {
     int mp,sp,vp;if(!Int32.TryParse(model.Text,out mp)||!Int32.TryParse(frps.Text,out sp)||!Int32.TryParse(visitor.Text,out vp)||mp<1||mp>65535||sp<1||sp>65535||vp<1||vp>65535)throw new ArgumentException("端口必须是 1–65535 的整数。");
     string url=PanelPreferences.WebUrl(web.Text);bool portChanged=ports[0]!=sp||ports[1]!=mp||PanelPreferences.Port(values,"ollamaPort",11434)!=mp;
     busy=true;UpdateButtons();save.IsEnabled=false;close.IsEnabled=false;operation=new PanelOperationLock();
     if(!PanelPreferences.Same(originalPref,PanelPreferences.Bytes(prefPath))||!PanelPreferences.Same(originalConfig,PanelPreferences.Bytes(configPath)))throw new IOException("配置已被其他程序修改，请关闭后重新打开设置。");
     values["ollamaPort"]=mp;values["visitorPort"]=vp;values["webChatUrl"]=url;
     byte[] prefsAfter=new UTF8Encoding(false).GetBytes(json.Serialize(values));
     if(portChanged){var actual=await Request("Status",null);var statusValue=D(actual,"Status");if(!B(actual,"Success")||statusValue==null)throw new IOException("未能确认隧道状态，端口未修改。");if(Convert.ToInt32(statusValue["FrpcCount"])>0)throw new IOException("请先关闭设置，点击 frp 卡片的停止，再修改端口。现有连接未中断。");
      byte[] proposed=new UTF8Encoding(false).GetBytes(PortConfig.Change(new UTF8Encoding(false,true).GetString(originalConfig),sp,mp));
      var plan=new Dictionary<string,object>{{"configBefore",PanelPreferences.Hash(originalConfig)},{"configAfter",PanelPreferences.Hash(proposed)},{"prefsBefore",PanelPreferences.Hash(originalPref)},{"prefsAfter",PanelPreferences.Hash(prefsAfter)}};
      PanelPreferences.Write(pending,null,new UTF8Encoding(false).GetBytes(json.Serialize(plan)),null);changed=PortConfig.Apply(configPath,originalConfig,sp,mp);configChanged=!PanelPreferences.Same(originalConfig,changed);}
     PanelPreferences.Write(prefPath,originalPref,prefsAfter,prefPath+".previous");committed=true;if(File.Exists(pending))File.Delete(pending);
     History("已保存端口与客户端设置。模型/frp 端口需点击启动使用；其他实例未停止。");actionError="";lastProbe=DateTime.MinValue;if(portChanged){state=null;Text("HeaderState").Text="端口已保存 · 等待启动";Text("OllamaState").Text="待检查";Text("OllamaDetail").Text="127.0.0.1:"+mp+" · 等待检查";Text("TunnelState").Text="待检查";}busy=false;dialog.Close();
     Banner(portChanged?"端口已保存，请点击右上启动。旧端口上的服务仍保留；远端设备需重新复制对应配置。":"设置已保存，网页地址与接收端口立即生效。","#E0F3EB");
    }catch(Exception ex){
     if(!committed&&configChanged){try{PanelPreferences.Write(configPath,changed,originalConfig,null);}catch{status.Text="保存中断且检测到外部变化，请核对配置后再启动。恢复点为 .port-previous.bak。";return;}}
     if(!committed&&File.Exists(pending)){try{PanelPreferences.RecoverPorts(root);}catch{status.Text="保存中断，原记录已保留；请重新打开设置核对。";return;}}
     status.Text=ex.Message;
    }finally{if(operation!=null)operation.Dispose();busy=false;save.IsEnabled=true;close.IsEnabled=true;UpdateButtons();}
    if(committed&&!closed)await Refresh();
   };
   dialog.Closing+=(s,e)=>{if(busy)e.Cancel=true;};dialog.Content=new ScrollViewer{Content=content,VerticalScrollBarVisibility=ScrollBarVisibility.Auto};dialog.ShowDialog();
  }
 }
}
