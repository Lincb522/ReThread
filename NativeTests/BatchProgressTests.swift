import Testing
import Foundation
@testable import CodexConversations
struct BatchProgressTests {
    @Test func progressFieldsAndElapsedTime() throws {
        let json = #"{"id":"test","mode":"execute","state":"running","phase":"official_rpc","completed":0,"total":7,"current_title":"待处理会话","current_index":1,"started_at":100,"phase_started_at":160,"bytes_done":1024,"bytes_total":4096,"stop_requested":false}"#
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let job = try decoder.decode(BatchJob.self, from: Data(json.utf8))
        #expect(job.isRunning); #expect(job.title == "Codex 正在处理")
        #expect(job.currentTitle == "待处理会话"); #expect(job.currentIndex == 1)
        #expect(job.bytesDone == 1024); #expect(job.stopRequested == false)
        #expect(BatchJob.elapsedLabel(since: job.startedAt, now: Date(timeIntervalSince1970: 178)) == "1 分 18 秒")
        #expect(BatchJob.elapsedLabel(since: job.phaseStartedAt, now: Date(timeIntervalSince1970: 178)) == "18 秒")
    }
    @Test func statusRetriesOnlyTransientReads() {
        #expect(ConversationStore.canRetryBatchStatus(URLError(.timedOut), failures: 1))
        #expect(!ConversationStore.canRetryBatchStatus(URLError(.timedOut), failures: 6))
        #expect(!ConversationStore.canRetryBatchStatus(URLError(.cancelled), failures: 1))
        #expect(!ConversationStore.canRetryBatchStatus(AppFailure.message("HTTP 404"), failures: 1))
        #expect(!ConversationStore.canRetryBatchStatus(DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "invalid")), failures: 1))
    }
}
