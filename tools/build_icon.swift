// Draw the same vector mark as assets/icon.svg with native AppKit.
import AppKit
import Foundation
let out = CommandLine.arguments[1]
try FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
for (name, pixels) in [("icon_16x16.png",16),("icon_16x16@2x.png",32),("icon_32x32.png",32),("icon_32x32@2x.png",64),("icon_128x128.png",128),("icon_128x128@2x.png",256),("icon_256x256.png",256),("icon_256x256@2x.png",512),("icon_512x512.png",512),("icon_512x512@2x.png",1024)] {
 let bitmap = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:pixels,pixelsHigh:pixels,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
 NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep:bitmap)
 let scale = CGFloat(pixels)/1024; let transform = AffineTransform(scale:scale); (transform as NSAffineTransform).concat()
 let bg = NSBezierPath(roundedRect:NSRect(x:40,y:40,width:944,height:944),xRadius:212,yRadius:212)
 NSGradient(starting:NSColor(red:0.08,green:0.17,blue:0.31,alpha:1),ending:NSColor(red:0.15,green:0.40,blue:0.83,alpha:1))!.draw(in:bg,angle:-45)
 NSColor.white.setFill(); NSBezierPath(roundedRect:NSRect(x:196,y:328,width:252,height:460),xRadius:42,yRadius:42).fill()
 func line(_ points:[NSPoint],_ width:CGFloat,_ color:NSColor) { color.setStroke();let p=NSBezierPath();p.move(to:points[0]);for point in points.dropFirst(){p.line(to:point)};p.lineWidth=width;p.lineCapStyle = .round;p.lineJoinStyle = .round;p.stroke() }
 let blue=NSColor(red:0.27,green:0.47,blue:0.74,alpha:1)
 line([NSPoint(x:245,y:700),NSPoint(x:399,y:700)],20,blue);line([NSPoint(x:245,y:634),NSPoint(x:399,y:634)],20,blue)
 NSColor(red:0.21,green:0.8,blue:0.74,alpha:1).setFill();NSBezierPath(ovalIn:NSRect(x:300,y:393,width:44,height:44)).fill()
 NSColor.white.setStroke();let screen=NSBezierPath(roundedRect:NSRect(x:568,y:426,width:280,height:240),xRadius:28,yRadius:28);screen.lineWidth=30;screen.stroke()
 line([NSPoint(x:539,y:374),NSPoint(x:879,y:374)],30,.white)
 let aqua=NSColor(red:0.40,green:0.90,blue:0.86,alpha:1)
 line([NSPoint(x:433,y:549),NSPoint(x:584,y:549)],32,aqua);line([NSPoint(x:546,y:587),NSPoint(x:584,y:549),NSPoint(x:546,y:511)],32,aqua)
 NSGraphicsContext.restoreGraphicsState()
 try bitmap.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:out).appendingPathComponent(name))
}
