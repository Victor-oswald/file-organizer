#define PY_SSIZE_T_CLEAN
#include <Python.h>

/* dot(ids_a, w_a, ids_b, w_b): merge-join of two sorted sparse vectors.
   ids are int32 arrays (sorted ascending), weights are float64 arrays. */
static PyObject *dot(PyObject *self, PyObject *args) {
    Py_buffer ia, wa, ib, wb;
    if (!PyArg_ParseTuple(args, "y*y*y*y*", &ia, &wa, &ib, &wb)) return NULL;
    const int32_t *A = (const int32_t *)ia.buf, *B = (const int32_t *)ib.buf;
    const double *WA = (const double *)wa.buf, *WB = (const double *)wb.buf;
    Py_ssize_t na = ia.len / 4, nb = ib.len / 4, i = 0, j = 0;
    double sum = 0.0;
    while (i < na && j < nb) {
        if (A[i] == B[j]) { sum += WA[i] * WB[j]; i++; j++; }
        else if (A[i] < B[j]) i++;
        else j++;
    }
    PyBuffer_Release(&ia); PyBuffer_Release(&wa); PyBuffer_Release(&ib); PyBuffer_Release(&wb);
    return PyFloat_FromDouble(sum);
}
static PyMethodDef M[] = {{"dot", dot, METH_VARARGS, "sparse dot"}, {NULL, NULL, 0, NULL}};
static struct PyModuleDef D = {PyModuleDef_HEAD_INIT, "fastdot", NULL, -1, M};
PyMODINIT_FUNC PyInit_fastdot(void) { return PyModule_Create(&D); }
