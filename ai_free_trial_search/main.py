from scheduler.dispatcher import SearchDispatcher
from collector.multi_source_collector import MultiSourceCollector
from cleaner.data_cleaner import DataCleaner
from aggregator.info_aggregator import InfoAggregator
from storage.local_storage import LocalStorage
from validator.manual_validator import ManualValidator
from frontend.app import FrontendComponent

def main():
    frontend = FrontendComponent()
    dispatcher = SearchDispatcher()
    collector = MultiSourceCollector()
    cleaner = DataCleaner()
    aggregator = InfoAggregator()
    storage = LocalStorage()
    validator = ManualValidator()
    
    user_query = frontend.handle_user_query("AI免费试用")
    dispatch_result = dispatcher.dispatch_search_request(user_query)
    raw_data = collector.collect_from_search_tool(dispatch_result)
    cleaned_data = cleaner.deduplicate(raw_data)
    cleaned_data = cleaner.filter_invalid_info(cleaned_data)
    cleaned_data = cleaner.structure_data(cleaned_data)
    aggregated_data = aggregator.aggregate_trial_info(cleaned_data)
    storage.save_search_result(aggregated_data)
    results = storage.query_history("AI免费试用")
    frontend.display_results(results)

if __name__ == "__main__":
    main()