import time
import os
import sys
import psutil
import concurrent.futures

# Ensure project root is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.matcher import match_item
from app.db_manager import init_db, DB_PATH

def run_performance_test():
    print("=================== 性能与内存压力测试 ===================")
    
    # 1. Warm up and initialize DB
    if not os.path.exists(DB_PATH):
        proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        xlsx_path = os.path.join(proj_dir, "doc", "sample", "物料明细.xlsx")
        init_db(xlsx_path)
        
    print("正在进行模型和索引预热...")
    # Warm up SentenceTransformer model and FAISS caches
    from app.vector_index import index_manager
    index_manager.encode_queries(["医用空调机组"])
    match_item({
        "name": "暖通风机",
        "specification": "风量:2000m3/h, 功率:1.5kW",
        "category": "风机"
    })
    
    # Get initial memory usage after warm up
    process = psutil.Process(os.getpid())
    mem_before = process.memory_info().rss / (1024 * 1024) # MB
    print(f"测试前内存占用 (预热后): {mem_before:.2f} MB")
    
    # Create 2000 items workload (simulating realistic concurrent batch processing)
    # Replicating base queries with index suffixes to simulate variety
    base_queries = [
        {
            "name": "医用洁卫型空调机组(室外型)",
            "specification": "机组风量:8000m3/h；新风量:2400m3/h；余压:650Pa；功率:5.5kw",
            "category": "医用洁净型组合式空调机组"
        },
        {
            "name": "防火阀",
            "specification": "70℃ DN200 镀锌板",
            "category": "阀门"
        },
        {
            "name": "给水管",
            "specification": "不锈钢管 DN100 PN16",
            "category": "给水管道"
        },
        {
            "name": "配电箱",
            "specification": "XM-100A AC380V 4P 30kW",
            "category": "配电柜"
        }
    ]
    
    workload = []
    for i in range(2000):
        base = base_queries[i % len(base_queries)].copy()
        # Add slight modifications to prevent simple caching
        if "风量" in base["specification"]:
            base["specification"] = f"机组风量:{8000 + i%10}m3/h；新风量:2400m3/h；余压:650Pa；功率:5.5kw"
        elif "DN200" in base["specification"]:
            base["specification"] = f"70℃ DN{200 + i%20} 镀锌板"
        workload.append(base)
        
    print(f"已生成 2000 行测试样本，开始多线程并发匹配...")
    
    start_time = time.time()
    
    # 2. Batch encode queries to avoid PyTorch CPU contention
    from app.vector_index import index_manager
    texts_to_encode = [f"{item['name']} {item['specification']}".strip() for item in workload]
    print(f"正在进行 2000 行文本的批量向量化...")
    embeddings = index_manager.encode_queries(texts_to_encode)
    print(f"向量化完成，耗时: {time.time() - start_time:.2f} 秒。开始并发匹配...")
    
    # Run batch matching using ThreadPoolExecutor (V2.2 batch scheduler)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(match_item, item, q_vec) for item, q_vec in zip(workload, embeddings)]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())
            
    total_time = time.time() - start_time
    avg_time = (total_time / 2000) * 1000 # ms per row
    
    mem_after = process.memory_info().rss / (1024 * 1024) # MB
    mem_growth = mem_after - mem_before
    
    print(f"匹配完成！总耗时: {total_time:.2f} 秒")
    print(f"单行平均耗时: {avg_time:.2f} 毫秒 (目标: <= 150ms)")
    print(f"测试后内存占用: {mem_after:.2f} MB")
    print(f"内存净增长: {mem_growth:.2f} MB (目标: <= 250MB)")
    
    # 2. Performance boundary validations
    assert avg_time <= 150.0, f"性能未达标: 单行耗时为 {avg_time:.2f}ms"
    assert mem_growth <= 250.0, f"内存未达标: 内存增加了 {mem_growth:.2f}MB"
    print("SUCCESS: 所有性能与内存约束指标完全达标！测试通过。")
    print("=========================================================")

if __name__ == "__main__":
    run_performance_test()
