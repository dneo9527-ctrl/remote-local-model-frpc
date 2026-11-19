using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace LocalAIControl {
 // Deliberately a conservative single-line TOML reader, not a general TOML parser.
 // Unsupported syntax is refused instead of guessing which private field to edit.
 public static class PortConfig {
  sealed class Value {
   public string Key, Text, StringValue;
   public int Offset, Length;
   public bool IsString;
  }
  sealed class Block {
   public readonly Dictionary<string,Value> Values=new Dictionary<string,Value>(StringComparer.Ordinal);
  }
  sealed class Parsed {
   public Value Server, Local;
  }
  static Exception Invalid() {return new InvalidDataException("配置不是可安全编辑的单行 TOML，或缺少唯一的 Ollama STCP 端口；请保留原文件并手动核对。");}
  static void ValidPort(int port) {if(port<1||port>65535)throw new ArgumentOutOfRangeException("port","端口必须在 1–65535 之间。");}
  static int Number(Value value) {
   int port;
   if(value==null||value.IsString||!Regex.IsMatch(value.Text,@"^[1-9][0-9]{0,4}$")||!Int32.TryParse(value.Text,NumberStyles.None,CultureInfo.InvariantCulture,out port))throw Invalid();
   ValidPort(port);return port;
  }
  static string ReadString(string raw) {
   if(raw.Length<2)throw Invalid();
   char quote=raw[0];
   if((quote!='\''&&quote!='"')||raw[raw.Length-1]!=quote||raw.StartsWith("\"\"\"",StringComparison.Ordinal)||raw.StartsWith("'''",StringComparison.Ordinal))throw Invalid();
   var text=new StringBuilder();
   for(int i=1;i<raw.Length-1;i++) {
    char c=raw[i];
    if(c==quote)throw Invalid();
    if(c<' '&&c!='\t')throw Invalid();
    if(quote=='"'&&c=='\\') {
     if(++i>=raw.Length-1)throw Invalid();
     char escaped=raw[i];
     switch(escaped) {
      case '"':text.Append('"');break;case '\\':text.Append('\\');break;
      case 'b':text.Append('\b');break;case 't':text.Append('\t');break;
      case 'n':text.Append('\n');break;case 'f':text.Append('\f');break;case 'r':text.Append('\r');break;
      case 'u':case 'U':
       int digits=escaped=='u'?4:8;
       if(i+digits>=raw.Length-1)throw Invalid();
       string hex=raw.Substring(i+1,digits);int scalar;
       if(!Regex.IsMatch(hex,@"^[0-9a-fA-F]+$")||!Int32.TryParse(hex,NumberStyles.AllowHexSpecifier,CultureInfo.InvariantCulture,out scalar)||scalar<0||scalar>0x10ffff||(scalar>=0xd800&&scalar<=0xdfff))throw Invalid();
       text.Append(Char.ConvertFromUtf32(scalar));i+=digits;break;
      default:throw Invalid();
     }
    } else text.Append(c);
   }
   return text.ToString();
  }
  static string Code(string line) {
   char quote='\0';bool escaped=false;
   for(int i=0;i<line.Length;i++) {
    char c=line[i];
    if(c<' '&&c!='\t')throw Invalid();
    if(quote=='"'&&escaped){escaped=false;continue;}
    if(quote=='"'&&c=='\\'){escaped=true;continue;}
    if(quote!='\0'){if(c==quote)quote='\0';continue;}
    if(c=='#')return line.Substring(0,i);
    if(c=='\''||c=='"')quote=c;
   }
   if(quote!='\0'||escaped)throw Invalid();return line;
  }
  static Parsed Parse(string toml) {
   if(String.IsNullOrEmpty(toml)||toml.Length>2*1024*1024)throw Invalid();
   var top=new Block();var proxies=new List<Block>();Block current=top;
   var tables=new HashSet<string>(StringComparer.Ordinal);bool root=true;
   for(int position=0;position<toml.Length;) {
    int end=position;while(end<toml.Length&&toml[end]!='\r'&&toml[end]!='\n')end++;
    string line=toml.Substring(position,end-position);int skip=position==0&&line.Length>0&&line[0]=='\ufeff'?1:0;
    string code=Code(line.Substring(skip));
    if(code.IndexOf("{{",StringComparison.Ordinal)>=0||code.IndexOf("${",StringComparison.Ordinal)>=0||code.IndexOf("<%",StringComparison.Ordinal)>=0)throw Invalid();
    string trimmed=code.Trim();
    if(trimmed.Length>0) {
     if(trimmed[0]=='[') {
      var header=Regex.Match(trimmed,@"^(?<array>\[\[)(?<name>[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\]\]$");
      if(header.Success) {
       // Only the documented proxy and visitor array tables can be safely partitioned here.
       string name=header.Groups["name"].Value;
       if(name!="proxies"&&name!="visitors")throw Invalid();
       current=new Block();if(name=="proxies")proxies.Add(current);root=false;
      } else {
       header=Regex.Match(trimmed,@"^\[(?<name>[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\]$");
       if(!header.Success)throw Invalid();string name=header.Groups["name"].Value;
       // Nested proxy/visitor tables may repeat for distinct array members; none are edited.
       if(!name.StartsWith("proxies.",StringComparison.Ordinal)&&!name.StartsWith("visitors.",StringComparison.Ordinal)&&!tables.Add(name))throw Invalid();
       current=new Block();root=false;
      }
     } else {
      var assignment=Regex.Match(code,@"^[\t ]*(?<key>[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)[\t ]*=[\t ]*(?<value>.*?)[\t ]*$");
      if(!assignment.Success)throw Invalid();
      string key=assignment.Groups["key"].Value,raw=assignment.Groups["value"].Value;
      if(raw.Length==0||current.Values.ContainsKey(key))throw Invalid();
      var value=new Value{Key=key,Text=raw,Offset=position+skip+assignment.Groups["value"].Index,Length=raw.Length};
      if(raw[0]=='\''||raw[0]=='"'){value.IsString=true;value.StringValue=ReadString(raw);}
      else if(!Regex.IsMatch(raw,@"^(?:true|false|[+-]?(?:0|[1-9][0-9_]*))$"))throw Invalid();
      current.Values.Add(key,value);
      if(!root&&key=="serverPort") { /* Nested serverPort is deliberately left untouched. */ }
     }
    }
    position=end;
    if(position<toml.Length&&toml[position]=='\r')position++;
    if(position<toml.Length&&toml[position]=='\n')position++;
   }
   Value server; if(!top.Values.TryGetValue("serverPort",out server))throw Invalid();
   Block selected=null;
   foreach(var proxy in proxies) {
    Value name,type;
    if(!proxy.Values.TryGetValue("name",out name)||!name.IsString||!proxy.Values.TryGetValue("type",out type)||!type.IsString)throw Invalid();
    if(name.StringValue=="ollama") {
     if(type.StringValue!="stcp"||selected!=null)throw Invalid();selected=proxy;
    }
   }
   Value local;if(selected==null||!selected.Values.TryGetValue("localPort",out local))throw Invalid();
   Number(server);Number(local);return new Parsed{Server=server,Local=local};
  }
  public static int[] Read(string originalToml) {
   var parsed=Parse(originalToml);return new[]{Number(parsed.Server),Number(parsed.Local)};
  }
  public static string Change(string originalToml,int serverPort,int localPort) {
   ValidPort(serverPort);ValidPort(localPort);var parsed=Parse(originalToml);
   var changes=new[]{parsed.Server,parsed.Local};Array.Sort(changes,(a,b)=>b.Offset.CompareTo(a.Offset));
   string result=originalToml;
   foreach(var value in changes) {
    int number=Object.ReferenceEquals(value,parsed.Server)?serverPort:localPort;
    if(Number(value)==number)continue;
    result=result.Remove(value.Offset,value.Length).Insert(value.Offset,number.ToString(CultureInfo.InvariantCulture));
   }
   return result;
  }
  static bool Equal(byte[] left,byte[] right) {
   if(left==null||right==null||left.Length!=right.Length)return false;
   int difference=0;for(int i=0;i<left.Length;i++)difference|=left[i]^right[i];return difference==0;
  }
  static byte[] ReadAll(Stream input) {using(var copy=new MemoryStream()){input.CopyTo(copy);return copy.ToArray();}}
  static string LockName(string path) {
   using(var hash=SHA256.Create())return "Local\\LocalAI-PortConfig-"+BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(path.ToUpperInvariant()))).Replace("-","");
  }
  public static byte[] Apply(string path,byte[] expectedBytes,int serverPort,int localPort) {
   if(String.IsNullOrEmpty(path)||!Path.IsPathRooted(path)||expectedBytes==null)throw new ArgumentException("请提供明确配置路径与读取时的原始字节。");
   path=Path.GetFullPath(path);ValidPort(serverPort);ValidPort(localPort);
   if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new IOException("不编辑符号链接配置。");
   var utf8=new UTF8Encoding(false,true);
   byte[] candidate=utf8.GetBytes(Change(utf8.GetString(expectedBytes),serverPort,localPort));
   string temporary=path+".ports-"+Guid.NewGuid().ToString("N")+".tmp";bool owned=false;
   using(var mutex=new Mutex(false,LockName(path))) {
    try {
     try{owned=mutex.WaitOne(0);}catch(AbandonedMutexException){owned=true;}
     if(!owned)throw new IOException("另一个端口保存正在进行，请稍后重试。");
     // Deny in-place writers while preparing the replacement. Delete sharing permits
     // atomic File.Replace; arbitrary external rename editors still need the same
     // coordination and cannot be made filesystem-CAS participants by this helper.
     using(var original=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read|FileShare.Delete)) {
      if(!Equal(ReadAll(original),expectedBytes))throw new IOException("配置已被其他程序修改，未保存端口；请重新读取。");
      if(Equal(candidate,expectedBytes))return candidate;
      using(var output=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None)){output.Write(candidate,0,candidate.Length);output.Flush(true);}
      // Re-read the current path immediately before the atomic replacement; never
      // use the earlier handle to mistake a replaced inode for the current file.
      using(var current=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read|FileShare.Delete)) {
       if(!Equal(ReadAll(current),expectedBytes))throw new IOException("配置已被其他程序修改，未保存端口；请重新读取。");
       File.Replace(temporary,path,path+".port-previous.bak");
      }
     }
     return candidate;
    } finally {
     if(File.Exists(temporary))try{File.Delete(temporary);}catch{}
     if(owned)mutex.ReleaseMutex();
    }
   }
  }
 }
}
