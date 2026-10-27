/* Provenance: tsp-h5ed.46 evidence/68.2/opaque_fd_route_probe.c */
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

static void fail(const char *what, VkResult result)
{
    fprintf(stderr, "probe setup failure: %s: VkResult=%d\n", what, result);
    exit(2);
}

int main(void)
{
    const VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "opaque-fd-route-probe",
        .apiVersion = VK_API_VERSION_1_2,
    };
    const VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };
    VkInstance instance = VK_NULL_HANDLE;
    VkResult result = vkCreateInstance(&instance_info, NULL, &instance);
    if (result != VK_SUCCESS)
        fail("vkCreateInstance", result);

    uint32_t physical_count = 1;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    result = vkEnumeratePhysicalDevices(instance, &physical_count, &physical);
    if (result != VK_SUCCESS || physical_count != 1)
        fail("vkEnumeratePhysicalDevices", result);

    VkPhysicalDeviceProperties properties;
    VkPhysicalDeviceMemoryProperties memory_properties;
    vkGetPhysicalDeviceProperties(physical, &properties);
    vkGetPhysicalDeviceMemoryProperties(physical, &memory_properties);
    if (memory_properties.memoryTypeCount != 1) {
        fprintf(stderr, "probe setup failure: memoryTypeCount=%u\n",
                memory_properties.memoryTypeCount);
        return 2;
    }

    uint32_t queue_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
    VkQueueFamilyProperties *queues = calloc(queue_count, sizeof(*queues));
    if (!queues)
        return 2;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, queues);
    uint32_t queue_family = UINT32_MAX;
    for (uint32_t i = 0; i < queue_count; i++) {
        if (queues[i].queueCount) {
            queue_family = i;
            break;
        }
    }
    free(queues);
    if (queue_family == UINT32_MAX)
        return 2;

    const float priority = 1.0f;
    const VkDeviceQueueCreateInfo queue_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = queue_family,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };
    const char *extensions[] = { VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME };
    const VkDeviceCreateInfo device_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &queue_info,
        .enabledExtensionCount = 1,
        .ppEnabledExtensionNames = extensions,
    };
    VkDevice device = VK_NULL_HANDLE;
    result = vkCreateDevice(physical, &device_info, NULL, &device);
    if (result != VK_SUCCESS)
        fail("vkCreateDevice", result);

    const VkExportMemoryAllocateInfo export_info = {
        .sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,
        .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT,
    };
    const VkMemoryAllocateInfo allocation_info = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext = &export_info,
        .allocationSize = 4096,
        .memoryTypeIndex = 0,
    };
    VkDeviceMemory memory = VK_NULL_HANDLE;
    result = vkAllocateMemory(device, &allocation_info, NULL, &memory);
    if (result != VK_SUCCESS)
        fail("vkAllocateMemory(OPAQUE_FD)", result);

    PFN_vkGetMemoryFdKHR get_memory_fd =
        (PFN_vkGetMemoryFdKHR)vkGetDeviceProcAddr(device, "vkGetMemoryFdKHR");
    if (!get_memory_fd) {
        fprintf(stderr, "probe setup failure: vkGetMemoryFdKHR unavailable\n");
        return 2;
    }
    const VkMemoryGetFdInfoKHR fd_info = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
        .memory = memory,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT,
    };
    int fd = -1;
    result = get_memory_fd(device, &fd_info, &fd);
    if (result != VK_SUCCESS)
        fail("vkGetMemoryFdKHR(OPAQUE_FD)", result);

    struct stat st;
    int fstat_result = fstat(fd, &st);
    void *mapped = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    int mmap_errno = mapped == MAP_FAILED ? errno : 0;
    bool roundtrip = false;
    if (mapped != MAP_FAILED) {
        volatile uint8_t *bytes = mapped;
        bytes[0] = 0x5a;
        roundtrip = bytes[0] == 0x5a;
        munmap(mapped, 4096);
    }

    printf("device=%s memoryTypeCount=%u type0Flags=0x%x allocateResult=%d "
           "getFdResult=%d fd=%d fstatResult=%d fdSize=%lld mmapErrno=%d roundtrip=%d\n",
           properties.deviceName, memory_properties.memoryTypeCount,
           memory_properties.memoryTypes[0].propertyFlags, VK_SUCCESS, result, fd,
           fstat_result, fstat_result ? -1LL : (long long)st.st_size,
           mmap_errno, roundtrip);

    close(fd);
    vkFreeMemory(device, memory, NULL);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);

    if (fstat_result || mmap_errno || !roundtrip) {
        fprintf(stderr, "probe mismatch: opaque fd is not usable backing\n");
        return 1;
    }
    puts("route feasibility confirmed: exact lavapipe exports usable opaque-fd memory");
    return 0;
}
