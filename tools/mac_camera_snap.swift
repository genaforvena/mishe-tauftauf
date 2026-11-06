import AVFoundation
import Foundation
let out = CommandLine.arguments[1]
let s = AVCaptureSession()
guard let dev = AVCaptureDevice.default(for: .video), let inp = try? AVCaptureDeviceInput(device: dev) else { print("nodevice"); exit(2) }
s.addInput(inp)
let o = AVCaptureStillImageOutput()
o.outputSettings = [AVVideoCodecKey: AVVideoCodecJPEG]
s.addOutput(o)
s.startRunning()
Thread.sleep(forTimeInterval: 3.0)
guard let c = o.connection(with: .video) else { print("noconn"); exit(3) }
var done = false
o.captureStillImageAsynchronously(from: c) { buf, err in
  if let b = buf, let d = AVCaptureStillImageOutput.jpegStillImageNSDataRepresentation(b) { try? d.write(to: URL(fileURLWithPath: out)); print("ok \(d.count)") } else { print("err \(String(describing: err))") }
  done = true
}
let end = Date().addingTimeInterval(10)
while !done && Date() < end { RunLoop.current.run(until: Date().addingTimeInterval(0.1)) }
s.stopRunning()
