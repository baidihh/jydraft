// 运行时 dlopen 剪映自带的 libvideoeditor，调用 lvve::EncryptUtils 做草稿加解密。
// 不链接、不复制、不分发官方二进制；只在用户本机运行时加载。
#include <dlfcn.h>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

namespace {
constexpr const char* kDefaultLibrary =
    "/Applications/VideoFusion-macOS.app/Contents/Frameworks/libvideoeditor.dylib";

using EnableFn = void (*)(void*, bool);
using EncryptFn = std::string (*)(void*, const std::string&);
using DecryptFn = std::string (*)(void*, const std::string&, const std::string&, bool&);

struct Symbols {
  void* handle = nullptr;
  EnableFn enable = nullptr;
  EncryptFn encrypt = nullptr;
  DecryptFn decrypt = nullptr;
};

Symbols load() {
  const char* env = std::getenv("JIANYING_LIBVIDEOEDITOR");
  const char* path = (env && *env) ? env : kDefaultLibrary;
  Symbols symbols;
  symbols.handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
  if (!symbols.handle) {
    throw std::runtime_error(std::string("无法加载 ") + path + ": " + dlerror());
  }
  auto bind = [&](const char* name) -> void* {
    void* value = dlsym(symbols.handle, name);
    if (!value) throw std::runtime_error(std::string("缺少符号: ") + name);
    return value;
  };
  symbols.enable = reinterpret_cast<EnableFn>(bind("_ZN4lvve12EncryptUtils6enableEb"));
  symbols.encrypt = reinterpret_cast<EncryptFn>(bind(
      "_ZN4lvve12EncryptUtils7encryptERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE"));
  symbols.decrypt = reinterpret_cast<DecryptFn>(bind(
      "_ZN4lvve12EncryptUtils7decryptERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES9_Rb"));
  return symbols;
}

std::string read_file(const char* path) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) throw std::runtime_error(std::string("无法读取 ") + path);
  return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
}

void write_file(const char* path, const std::string& payload) {
  std::ofstream stream(path, std::ios::binary | std::ios::trunc);
  if (!stream) throw std::runtime_error(std::string("无法写入 ") + path);
  stream.write(payload.data(), static_cast<std::streamsize>(payload.size()));
  if (!stream) throw std::runtime_error(std::string("写入失败 ") + path);
}

int run(const char* mode, const char* input, const char* output) {
  Symbols symbols = load();
  // EncryptUtils 只有少量状态成员；给足空间并清零，避免依赖具体 sizeof。
  alignas(16) unsigned char storage[4096] = {};
  void* instance = storage;
  symbols.enable(instance, true);
  const std::string payload = read_file(input);
  if (std::string(mode) == "decrypt") {
    bool valid = false;
    const std::string plaintext = symbols.decrypt(instance, payload, "{}", valid);
    if (!valid || plaintext.empty()) throw std::runtime_error("解密失败或结果为空");
    write_file(output, plaintext);
    std::cout << "decrypt input=" << payload.size() << " output=" << plaintext.size() << "\n";
    return 0;
  }
  if (std::string(mode) == "encrypt") {
    const std::string ciphertext = symbols.encrypt(instance, payload);
    if (ciphertext.empty()) throw std::runtime_error("加密结果为空");
    write_file(output, ciphertext);
    std::cout << "encrypt input=" << payload.size() << " output=" << ciphertext.size() << "\n";
    return 0;
  }
  throw std::runtime_error("用法: jycodec encrypt|decrypt INPUT OUTPUT");
}
}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc != 4) {
      std::cerr << "usage: jycodec encrypt|decrypt INPUT OUTPUT\n";
      return 64;
    }
    return run(argv[1], argv[2], argv[3]);
  } catch (const std::exception& error) {
    std::cerr << "error: " << error.what() << "\n";
    return 1;
  }
}
