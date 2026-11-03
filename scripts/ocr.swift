// Prints the text macOS Vision reads in each image (used to check screenshots for private terms).
import Foundation
import Vision
for path in CommandLine.arguments.dropFirst() {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    try VNImageRequestHandler(url: URL(fileURLWithPath: path)).perform([req])
    for o in req.results ?? [] { if let c = o.topCandidates(1).first { print(c.string) } }
}
