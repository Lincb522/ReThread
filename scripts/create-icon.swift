import AppKit
let args = CommandLine.arguments
let folder = URL(fileURLWithPath: args[1])
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
guard let logo = NSImage(contentsOfFile: args[2]) else { fatalError("Missing logo SVG") }
for size in [16,32,64,128,256,512,1024] {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: size*4, bitsPerPixel: 32)!
    NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let scale = CGFloat(size)/1024; let rect = NSRect(x: 72*scale, y: 72*scale, width: 880*scale, height: 880*scale)
    let shape = NSBezierPath(roundedRect: rect, xRadius: 207*scale, yRadius: 207*scale)
    NSGradient(starting: NSColor(calibratedRed: 0.26, green: 0.28, blue: 0.32, alpha: 1), ending: NSColor(calibratedRed: 0.16, green: 0.17, blue: 0.20, alpha: 1))!.draw(in: shape, angle: -65)
    NSColor(calibratedWhite: 0.45, alpha: 1).setStroke();shape.lineWidth = max(1,scale*4);shape.stroke()
    // Original Lucide vectors tinted to the design's silver foreground.
    let iconRect = NSRect(x: 260*scale,y: 260*scale,width: 504*scale,height: 504*scale)
    logo.draw(in: iconRect)
    NSGraphicsContext.restoreGraphicsState()
    let data = bitmap.representation(using: .png, properties: [:])!
    if size <= 512 { try data.write(to: folder.appendingPathComponent("icon_\(size)x\(size).png")) }
    if size >= 32 { try data.write(to: folder.appendingPathComponent("icon_\(size/2)x\(size/2)@2x.png")) }
}
