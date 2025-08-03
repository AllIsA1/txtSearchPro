import os
import re
import mmap
import time
import multiprocessing
import psutil
import humanize
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform
import cpuinfo
from pathlib import Path
import sys
import stat

class Config:
    # Автоматическая настройка потоков
    MAX_WORKERS = min(multiprocessing.cpu_count() * 2, 32)
    RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    MEMORY_LIMIT = 0.9
    CHUNK_SIZE = 1000000
    
    COLORS = {
        'header': '\033[96m\033[1m',
        'success': '\033[92m',
        'warning': '\033[93m',
        'error': '\033[91m',
        'info': '\033[94m',
        'progress': '\033[95m',
        'reset': '\033[0m'
    }

class TextSearchEngine:
    def __init__(self):
        self.stats = {
            'files_processed': 0,
            'matches_found': 0,
            'total_size': 0,
            'errors': 0
        }
        self.query = None
        self.cpu_info = self.get_cpu_info()
        self.setup_results_dir()

    def get_cpu_info(self):
        try:
            info = cpuinfo.get_cpu_info()
            return {
                'name': info.get('brand_raw', platform.processor()),
                'cores': psutil.cpu_count(logical=False),
                'threads': psutil.cpu_count(logical=True),
                'freq': f"{psutil.cpu_freq().current:.2f} GHz" if psutil.cpu_freq() else 'N/A'
            }
        except:
            return {
                'name': platform.processor(),
                'cores': multiprocessing.cpu_count(),
                'threads': os.cpu_count(),
                'freq': 'N/A'
            }

    def setup_results_dir(self):
        try:
            os.makedirs(Config.RESULTS_DIR, exist_ok=True)
            if os.name == 'posix':
                os.chmod(Config.RESULTS_DIR, stat.S_IRWXU)
            elif os.name == 'nt':
                import win32api
                import win32con
                win32api.SetFileAttributes(Config.RESULTS_DIR, win32con.FILE_ATTRIBUTE_NORMAL)
            
            test_file = os.path.join(Config.RESULTS_DIR, 'permission_test.txt')
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
        except Exception as e:
            print(f"{Config.COLORS['error']}❌ Ошибка создания папки результатов: {e}{Config.COLORS['reset']}")
            sys.exit(1)

    def ensure_write_permission(self, filepath):
        try:
            if os.path.exists(filepath):
                if os.name == 'posix':
                    os.chmod(filepath, stat.S_IWUSR | stat.S_IRUSR)
                elif os.name == 'nt':
                    import win32api
                    import win32con
                    win32api.SetFileAttributes(filepath, win32con.FILE_ATTRIBUTE_NORMAL)
            return True
        except:
            return False

    def search_file(self, file_info):
        matches = []
        try:
            with open(file_info['path'], 'r', encoding='utf-8', errors='ignore') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    for match in re.finditer(re.escape(self.query).encode('utf-8'), mm):
                        context = mm[max(0, match.start()-50):match.end()+50].decode('utf-8', errors='replace')
                        matches.append({
                            'file': file_info['path'],
                            'context': context.strip(),
                            'position': match.start()
                        })
        except Exception as e:
            self.stats['errors'] += 1
            return (file_info['path'], [], str(e))
        return (file_info['path'], matches, "")

    def save_results(self, matches):
        if not matches:
            return

        safe_query = re.sub(r'[\\/*?:"<>|]', '_', self.query)[:50]
        result_file = os.path.join(Config.RESULTS_DIR, f"results_{safe_query}.txt")
        
        try:
            self.ensure_write_permission(result_file)
            
            with open(result_file, 'w', encoding='utf-8') as f:
                f.write(f"Результаты поиска: '{self.query}'\n")
                f.write(f"Всего совпадений: {len(matches)}\n{'='*50}\n")
                for match in matches:
                    f.write(f"\nФайл: {match['file']}\nПозиция: {match['position']}\n")
                    f.write(f"Контекст: {match['context']}\n{'='*50}\n")
            
            print(f"{Config.COLORS['success']}✅ Результаты сохранены в: {result_file}{Config.COLORS['reset']}")
        except Exception as e:
            print(f"{Config.COLORS['error']}❌ Ошибка сохранения: {e}{Config.COLORS['reset']}")

    def run_search(self, folder):
        self.print_header()
        
        files = self.get_folder_stats(folder)
        if not files:
            print(f"{Config.COLORS['error']}❌ Не найдено .txt файлов!{Config.COLORS['reset']}")
            return

        print(f"{Config.COLORS['info']}▪ Папка: {folder}")
        print(f"{Config.COLORS['info']}▪ Файлов: {len(files)}")
        print(f"{Config.COLORS['info']}▪ Общий размер: {humanize.naturalsize(self.stats['total_size'])}")
        print(f"{'='*80}{Config.COLORS['reset']}\n")

        self.query = input(f"{Config.COLORS['header']}🔍 Введите поисковый запрос: {Config.COLORS['reset']}").strip()
        if not self.query:
            print(f"{Config.COLORS['error']}❌ Запрос не может быть пустым!{Config.COLORS['reset']}")
            return

        start_time = time.time()
        all_matches = []

        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
            futures = [executor.submit(self.search_file, file) for file in files]
            
            for future in tqdm(as_completed(futures), total=len(files),
                            desc=f"{Config.COLORS['progress']}Поиск (CPU)",
                            unit="файл"):
                path, matches, error = future.result()
                self.stats['files_processed'] += 1
                
                if error:
                    print(f"{Config.COLORS['warning']}⚠ {error}{Config.COLORS['reset']}")
                
                if matches:
                    self.stats['matches_found'] += len(matches)
                    all_matches.extend(matches)

        self.save_results(all_matches)
        
        print(f"\n{' Статистика ':=^80}")
        print(f"{Config.COLORS['info']}▪ Обработано: {self.stats['files_processed']}/{len(files)} файлов")
        print(f"{Config.COLORS['info']}▪ Найдено совпадений: {self.stats['matches_found']}")
        print(f"{Config.COLORS['info']}▪ Ошибок: {self.stats['errors']}")
        print(f"{Config.COLORS['info']}▪ Время: {time.time() - start_time:.2f} сек")
        print(f"{Config.COLORS['info']}▪ Памяти использовано: {humanize.naturalsize(psutil.Process().memory_info().rss)}")
        print(f"{'='*80}{Config.COLORS['reset']}")

    def get_folder_stats(self, folder):
        file_list = []
        total_size = 0
        
        for root, _, files in os.walk(folder):
            for file in files:
                if file.lower().endswith('.txt'):
                    path = os.path.join(root, file)
                    try:
                        size = os.path.getsize(path)
                        file_list.append({'path': path, 'size': size})
                        total_size += size
                    except Exception as e:
                        print(f"{Config.COLORS['warning']}⚠ Ошибка доступа к {file}: {e}{Config.COLORS['reset']}")
        
        self.stats['total_size'] = total_size
        return file_list

    def print_header(self):
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"{Config.COLORS['header']}\n{' TXT SEARCH PRO ':=^80}")
        print(f"{Config.COLORS['info']}▪ Процессор: {self.cpu_info['name']}")
        print(f"{Config.COLORS['info']}▪ Ядер/Потоков: {self.cpu_info['cores']}/{self.cpu_info['threads']}")
        print(f"{Config.COLORS['info']}▪ Частота: {self.cpu_info['freq']}")
        print(f"{Config.COLORS['info']}▪ ОЗУ: {humanize.naturalsize(psutil.virtual_memory().total)}")
        print(f"{Config.COLORS['info']}▪ Потоков поиска: {Config.MAX_WORKERS}")
        print(f"{Config.COLORS['info']}▪ Папка результатов: {Config.RESULTS_DIR}")
        print(f"{'='*80}{Config.COLORS['reset']}\n")

def main():
    print(f"{Config.COLORS['header']}\n=== TXT SEARCH PRO ===")
    print(f"Самый мощный поисковик в .txt файлах{Config.COLORS['reset']}")
    
    engine = TextSearchEngine()
    default_folder = os.path.join(os.path.dirname(__file__), "db")
    
    while True:
        try:
            folder = input(f"\n{Config.COLORS['header']}📂 Введите путь к папке (по умолчанию {default_folder}): {Config.COLORS['reset']}").strip()
            folder = folder or default_folder
            engine.run_search(folder)
            
            if input(f"\n{Config.COLORS['header']}Продолжить поиск? (y/n): {Config.COLORS['reset']}").lower() != 'y':
                break
                
        except KeyboardInterrupt:
            print(f"\n{Config.COLORS['warning']}Поиск прерван{Config.COLORS['reset']}")
            break
        except Exception as e:
            print(f"{Config.COLORS['error']}❌ Ошибка: {e}{Config.COLORS['reset']}")
            break
    
    input("\nНажмите Enter для выхода...")

if __name__ == "__main__":
    main()